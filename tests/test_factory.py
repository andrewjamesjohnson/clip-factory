"""The factory's rules, on throwaway folders: python3 -m unittest discover -s tests (from ~/clip-factory)."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="clipfactory-test-"))
os.environ["CLIP_FACTORY_STATE"] = str(TMP / "state")

# never reach a real account from a test: every key lookup points at an empty folder
from clipfactory import direct as _D     # noqa: E402
from clipfactory import outlets as _O    # noqa: E402
_D.TOKENS = _O.TOKENS = TMP / "no-keys"

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import copytext as T   # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import signoff as S    # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import stats as ST     # noqa: E402

DAY = date(2030, 1, 2)


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def deliver(root: Path, hhmm: str, slug: str, seconds: float, platforms=("youtube", "tiktok", "facebook", "reels")) -> Path:
    d = root / f"{hhmm} {slug}"
    d.mkdir(parents=True, exist_ok=True)
    clip(d / f"01-{slug}.mp4", seconds)
    for p in platforms:
        (d / f"{p}.txt").write_text(f"{slug} title\n\n{slug} words for {p}\n")
    return d


class FactoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(TMP / "drafts")
        raw.pop("inbox_auto_sign", None)          # the owner's switches stay off here: these tests are the old rules
        for a in raw["accounts"].values():
            a.pop("signed_by_hand_stays", None)
            a["delivered"] = [str(TMP / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}   # tiny clips keep the test fast; the rule is the same
        cls.cfg_path = TMP / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.cfg = C.load(cls.cfg_path)
        cls.acct = cls.cfg.account("gaming")
        cls.dl = cls.acct.delivered_dirs(DAY)[0]

    def test_1_plan_has_ten_slots_seven_to_eleven(self):
        with B.edit(self.cfg, self.acct, DAY) as bd:
            pass
        self.assertEqual(list(bd["slots"]), ["07:00", "08:45", "10:30", "12:15", "14:00", "15:45", "17:30", "19:15",
                                             "21:00", "23:00"])
        self.assertTrue(bd["slots"]["07:00"]["publish_at"].startswith("2030-01-02T07:00:00-07:00"))

    def test_2_collect_ready_refused_failed(self):
        deliver(self.dl, "0700", "good-one", 2.0)
        deliver(self.dl, "0845", "too-long", 4.0)
        deliver(self.dl, "1030", "no-words", 2.0, platforms=("youtube",))
        deliver(self.dl, "1200", "off-clock", 2.0)   # the skill's own slot: takes the first open one
        lines = K.collect(self.cfg, self.acct, DAY)
        bd = B.read(self.acct, DAY)
        self.assertEqual(bd["slots"]["07:00"]["status"], B.READY, lines)
        s845 = bd["slots"]["08:45"]          # refused, never drafted, and the slot stays fillable (gta-yield, 10-18)
        self.assertEqual((s845["status"], s845["fail_kind"]), (B.FAILED, "refused"))
        self.assertEqual([r["slug"] for r in s845["refused_clips"]], ["too-long"])
        self.assertNotIn("clip", s845)
        self.assertEqual(bd["slots"]["10:30"]["status"], B.FAILED)
        self.assertEqual(bd["slots"]["12:15"]["status"], B.READY)   # 1200 off-clock landed in the first open slot
        again = K.collect(self.cfg, self.acct, DAY)
        self.assertEqual(B.counts(B.read(self.acct, DAY)), B.counts(bd), again)  # collecting twice changes nothing

    def test_3_approve_signs_and_schedules_and_edits_void_the_signature(self):
        with self.assertRaises(SystemExit):
            S.approve(self.cfg, self.acct, DAY, ["07:00"], "Someone Else")
        out = S.approve(self.cfg, self.acct, DAY, ["07:00", "12:15"], "andrew", now=self.cfg.slot_time(DAY, "06:00"))
        self.assertTrue(all("goes out at" in ln for ln in out), out)       # one click: signed AND scheduled
        bd = B.read(self.acct, DAY)
        self.assertEqual(bd["slots"]["07:00"]["signoff"]["by"], "Andrew")
        self.assertEqual((bd["slots"]["07:00"]["status"], bd["slots"]["12:15"]["status"]), (B.SCHEDULED, B.SCHEDULED))
        self.assertEqual(B.verify(bd["slots"]["07:00"]), [])
        drafts = K.drafts_dir(self.cfg, self.acct, DAY)
        self.assertTrue((drafts / "Ready to post" / "tiktok" / "0700 good-one.mp4").exists())   # TikTok is by hand
        self.assertFalse((drafts / "Ready to post" / "youtube_shorts").exists())  # posts by itself: no copy to post twice
        Path(bd["slots"]["12:15"]["copy"]["tiktok"]["path"]).write_text("changed after signing\n")
        out = O.schedule(self.cfg, self.acct, DAY)
        self.assertTrue(any("12:15: NOT scheduled" in ln for ln in out), out)
        csv = (drafts / "SCHEDULE.csv").read_text()
        self.assertIn("07:00", csv)
        self.assertNotIn("12:15", csv)

    def test_4_remake_never_takes_the_declined_cut_again(self):
        out = S.decline(self.cfg, self.acct, DAY, ["12:15"], "Andrew", "weak", remake=True)
        self.assertIn("pull it first", out[0])                       # scheduled: Pull takes it back first
        self.assertIn("pulled", S.pull(self.cfg, self.acct, DAY, ["12:15"], "Andrew")[0])
        self.assertEqual(B.read(self.acct, DAY)["slots"]["12:15"]["status"], B.READY)
        S.decline(self.cfg, self.acct, DAY, ["12:15"], "Andrew", "weak", remake=True)
        K.collect(self.cfg, self.acct, DAY)
        bd = B.read(self.acct, DAY)
        self.assertEqual(bd["slots"]["12:15"]["status"], B.OPEN)
        deliver(self.dl, "1215", "second-try", 2.0)
        K.collect(self.cfg, self.acct, DAY)
        bd = B.read(self.acct, DAY)
        self.assertEqual(bd["slots"]["12:15"]["slug"], "second-try")

    def test_5_youtube_needs_a_sign_in_and_tiktok_needs_its_audit(self):
        acct = self.cfg.account("gaming")
        from clipfactory import direct as D
        orig = O.TOKENS
        O.TOKENS = TMP / "no-tokens"
        try:
            self.assertFalse(O.yt_connected(acct))
            with self.assertRaises(D.Refused) as e:
                O.youtube_upload(acct, {"slot": "07:00"})
        finally:
            O.TOKENS = orig
        self.assertIn("not connected", str(e.exception))
        acct.outlets["tiktok"] = {"mode": "api", "api_app_audited": False}
        with self.assertRaises(D.PlatformError) as e:
            D.tiktok_post(acct, Path("/nonexistent.mp4"), {"caption": "x"}, {})
        self.assertIn("not approved", str(e.exception))
        acct.outlets["tiktok"] = {"mode": "export"}

    def test_6_copy_shapes(self):
        gta = "HE DID NOT SEE THE TRAIN #shorts\n\nHe did not see the train.\n#gta6 #shorts"
        self.assertEqual(T.for_platform(gta, "youtube_shorts")["title"], "HE DID NOT SEE THE TRAIN #shorts")
        self.assertEqual(T.for_platform(gta, "tiktok")["title"], "")
        self.assertIn("HE DID NOT SEE", T.for_platform(gta, "tiktok")["caption"])
        politics = "# SLOT 12:00\n# notes\n\nTITLE\nA title\n\nDESCRIPTION\nThe body.\n#cfpb\n\nTAGS\ncfpb, oversight\n"
        w = T.for_platform(politics, "youtube_shorts")
        self.assertEqual((w["title"], w["tags"]), ("A title", "cfpb, oversight"))
        self.assertIn("#cfpb", w["caption"])
        self.assertNotIn("SLOT", w["caption"])

    def test_7_post_due_posts_signed_clips_once_at_their_time(self):
        day = date(2030, 1, 3)
        cfg = C.load(self.cfg_path)
        acct = cfg.account("gaming")
        acct.outlets["tiktok"] = {"mode": "api"}
        deliver(acct.delivered_dirs(day)[0], "0700", "due-one", 2.0)
        deliver(acct.delivered_dirs(day)[0], "0845", "edited-one", 2.0)
        deliver(acct.delivered_dirs(day)[0], "1030", "later-one", 2.0)
        K.collect(cfg, acct, day)
        S.approve(cfg, acct, day, None, "Andrew")                    # Approve schedules: nothing else to press
        self.assertEqual(B.counts(B.read(acct, day)).get(B.SCHEDULED), 3)
        Path(B.read(acct, day)["slots"]["08:45"]["copy"]["tiktok"]["path"]).write_text("changed after signing\n")
        with B.edit(cfg, acct, day, create=False) as bd:          # TikTok posts only once someone chose who sees it
            for s in bd["slots"].values():
                s["tiktok_post"] = {"privacy_level": "PUBLIC_TO_EVERYONE"}
        calls = []
        fake = {"tiktok": lambda a, clip, words, slot: calls.append(slot["slot"]) or {"post_id": "tt" + slot["slot"], "url": "https://t/x"}}
        now = cfg.slot_time(day, "09:30")
        out = P.post_due(cfg, now=now, posters=fake)
        self.assertEqual(calls, ["07:00"], out)                       # 08:45 was edited after signing, 10:30 not due
        bd = B.read(acct, day)
        self.assertEqual(bd["slots"]["07:00"]["outlets"]["tiktok"]["post_id"], "tt07:00")
        self.assertTrue(bd["slots"]["08:45"]["outlets"]["tiktok"]["gave_up"])
        P.post_due(cfg, now=now, posters=fake)
        self.assertEqual(calls, ["07:00"])                            # never twice
        boom = {"tiktok": lambda *a: (_ for _ in ()).throw(RuntimeError("platform said no"))}
        for i in range(5):                                            # one try per run, a run every 10 minutes
            P.post_due(cfg, now=cfg.slot_time(day, "11:00") + timedelta(minutes=10 * i), posters=boom)
        r = B.read(acct, day)["slots"]["10:30"]["outlets"]["tiktok"]
        self.assertEqual((r["tries"], r["gave_up"]), (3, True))        # three tries, then a person looks

    def test_8_stats_match_videos_to_clips_and_ids_match_the_dashboard(self):
        self.assertEqual(ST.fnv36("gaming|youtube|dQw4w9WgXcQ"), "h0bmj8")   # the page's hash() of the same string
        day = date(2030, 1, 3)
        cfg = C.load(self.cfg_path)
        acct = cfg.account("gaming")
        at = cfg.slot_time(day, "07:00")
        vids = [
            {"platform_id": "v1", "text": "due-one title", "published_at": at.isoformat(), "views": 900, "likes": 9},
            {"platform_id": "v2", "text": "something posted by hand", "published_at": at.isoformat(), "views": 50},
        ]
        lines = ST.refresh(cfg, days=36500, listers={"youtube_shorts": lambda a, since: vids if a.slug == "gaming" else []})
        bd = B.read(acct, day)
        self.assertEqual(bd["slots"]["07:00"].get("outlets", {}).get("youtube_shorts", {}).get("metrics", {}).get("views"), 900, lines)
        posts = ST.posts_from_boards(cfg, days=36500) + ST.pulled_posts(cfg)
        yt = [p for p in posts if p["platform"] == "youtube" and p["account"] == "gaming"]
        self.assertEqual(sorted(p["pid"] for p in yt), ["v1", "v2"])
        self.assertEqual(next(p for p in yt if p["pid"] == "v1")["clip"], "due-one")
        out = ST.write(cfg, posts, out=TMP / "stats")
        self.assertIn("dashboard documents", out[0])

    def test_9_direct_calls_against_a_fake_platform(self):
        from datetime import datetime, timezone
        from clipfactory import direct as D
        acct = self.cfg.account("politics")
        orig_tokens, orig_http, orig_sleep, orig_band = D.TOKENS, D._http, D.time.sleep, D.in_band
        D.TOKENS = TMP / "tokens"
        D.in_band = lambda *a: None          # the plumbing against a fake platform; the wall has its own test
        D._save(acct, "meta", {"page_id": "P1", "page_token": "PT", "ig_user_id": "IG1"})
        calls = []
        status = iter(["IN_PROGRESS", "FINISHED"])

        def fake(method, url, params=None, form=None, body=None, data=None, headers=None, timeout=0):
            calls.append((method, url.split("?")[0].replace(D.GRAPH, ""), form or params or {}))
            u = url.split("?")[0]
            if u.endswith("/IG1/media") and method == "POST":
                return {"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/C1"}
            if "rupload" in u:
                self.assertEqual(headers["offset"], "0")
                return {"success": True}
            if u.endswith("/C1"):
                return {"status_code": next(status)}
            if u.endswith("/media_publish"):
                return {"id": "M1"}
            if u.endswith("/M1"):
                return {"permalink": "https://instagram.com/reel/abc", "timestamp": "2030-01-03T12:00:00+0000"}
            if u.endswith("/P1/video_reels"):
                return {"data": [{"id": "R1", "description": "hearing clip", "created_time": "2030-01-03T12:00:00+0000",
                                  "permalink_url": "/reel/R1", "length": 41.5, "comments": {"summary": {"total_count": 4}}}]}
            if u.endswith("/R1/video_insights"):
                return {"data": [{"name": "blue_reels_play_count", "values": [{"value": 1200}]},
                                 {"name": "post_video_likes_by_reaction_type", "values": [{"value": {"REACTION_LIKE": 30, "REACTION_LOVE": 5}}]},
                                 {"name": "post_video_social_actions", "values": [{"value": 10}]}]}
            raise AssertionError(f"unexpected call {method} {url}")

        D._http, D.time.sleep = fake, lambda s: None
        try:
            mp4 = TMP / "x.mp4"
            mp4.write_bytes(b"0" * 100)
            r = D.ig_post(acct, mp4, {"caption": "words"}, {})
            self.assertEqual((r["post_id"], r["url"]), ("M1", "https://instagram.com/reel/abc"))
            self.assertEqual(calls[0][2]["media_type"], "REELS")
            vids = D.fb_list(acct, datetime(2030, 1, 1, tzinfo=timezone.utc))
            self.assertEqual(vids[0]["url"], "https://www.facebook.com/reel/R1")
            self.assertEqual((vids[0]["views"], vids[0]["likes"], vids[0]["comments"], vids[0]["shares"]), (1200, 35, 4, 6))
        finally:
            D.TOKENS, D._http, D.time.sleep, D.in_band = orig_tokens, orig_http, orig_sleep, orig_band
        self.assertEqual(D._iso_dur("PT45S"), 45)
        self.assertEqual(D._iso_dur("PT1M2S"), 62)

    def test_10_drop_folder_signs_and_schedules_into_the_next_open_slot(self):
        from clipfactory import inbox as I
        raw = json.loads(self.cfg_path.read_text())
        raw["inbox_auto_sign"] = True        # the old way, set on purpose: live drops wait for Approve (10-17)
        signs = TMP / "accounts-autosign.json"
        signs.write_text(json.dumps(raw))
        cfg = C.load(signs)
        cfg.inbox_root = TMP / "queue"
        acct = cfg.account("politics")
        day = date(2030, 2, 1)
        now = cfg.slot_time(day, "06:50")
        I.ensure(cfg)
        q = cfg.inbox_root / acct.name
        clip(q / "senator gets cornered.mp4", 2.0)
        (q / "senator gets cornered.txt").write_text("SENATOR GETS CORNERED\n\nThe whole exchange. #hearing\n")
        clip(q / "way too long.mp4", 4.0)
        clip(q / "still copying.mp4", 2.0)
        old = time.time() - 120
        for n in ("senator gets cornered.mp4", "way too long.mp4"):
            os.utime(q / n, (old, old))
        out = I.take(cfg, now=now)
        bd = B.read(acct, day)
        s7, s9 = bd["slots"]["07:00"], bd["slots"]["08:45"]
        self.assertEqual(s7["status"], B.OPEN, out)                  # 07:00 is 10 minutes away: too close
        self.assertEqual(s9["status"], B.SCHEDULED, out)            # signed and handed over
        self.assertEqual((s9["signoff"]["by"], s9["source"]), ("Andrew", "drop folder"))
        self.assertEqual(B.verify(s9), [])
        self.assertIn("SENATOR GETS CORNERED", Path(s9["copy"]["youtube_shorts"]["path"]).read_text())
        self.assertTrue((q / "Taken" / "2030-02-01 0845 senator gets cornered.mp4").exists())
        self.assertTrue((q / "Refused" / "way too long - why.txt").exists())
        self.assertTrue((q / "still copying.mp4").exists())           # left for the next run
        os.utime(q / "still copying.mp4", (old, old))
        I.take(cfg, now=now)
        self.assertEqual(B.read(acct, day)["slots"]["10:30"]["status"], B.SCHEDULED)   # the next one takes the next slot


class WallTest(unittest.TestCase):
    def test_a_dummy_never_reaches_a_platform(self):
        """2026-10-16: eight 2s test dummies reached the real Gaming channel. in_band refuses them."""
        from clipfactory import direct as D
        cfg = C.load(ROOT / "config" / "accounts.json")
        dummy = TMP / "dummy.mp4"
        clip(dummy, 2.0)
        owners = [s for s, a in cfg.all_accounts.items() if a.raw.get("owner") != "client"]   # switched on or not
        self.assertEqual(owners[:2], ["gaming", "politics"])
        for slug in owners:
            with self.assertRaises(D.PlatformError) as e:
                D.in_band(cfg.account(slug), dummy)
            self.assertIn("outside", str(e.exception))


if __name__ == "__main__":
    unittest.main()
