import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"]="1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())
"""The Stats tab's money, history and counting rules: two money figures, reassign rules (count by content or by
channel), merged pulled files, history snapshots, follower counts, scheduled YouTube uploads, 'Only me' TikTok
tests, board slots and the Politics's source. Throwaway folders and fake numbers only; nothing reaches the network."""
import json
import shutil
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B          # noqa: E402
from clipfactory import collect as K        # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import followers as FL     # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import stats as ST         # noqa: E402
from clipfactory import statspage as SP     # noqa: E402

DAY = date(2031, 3, 4)
UTC = timezone.utc


def iso(d: datetime) -> str:
    return d.astimezone(UTC).isoformat()


def video(pid: str, at: datetime, views: int = 10, **kw) -> dict:
    return dict({"platform_id": pid, "url": f"https://www.youtube.com/shorts/{pid}", "text": f"Video {pid}",
                 "published_at": iso(at), "dur": 40, "views": views, "likes": 1, "comments": 0, "shares": 0}, **kw)


class MoneyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-money-"))
        cls.old = (C.STATE, ST.OUT, O.TOKENS, D.TOKENS)
        C.STATE = cls.tmp / "state"
        ST.OUT = C.STATE / "stats"
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["review_port"] = 0
        for slug, a in raw["accounts"].items():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["account_dir"] = str(cls.tmp / "acct" / slug)
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))

    @classmethod
    def tearDownClass(cls):
        C.STATE, ST.OUT, O.TOKENS, D.TOKENS = cls.old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for d in ("state", "drafts", "delivered", "keys", "acct"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        SP._cache["key"] = None
        SP._run.update(running=False, finished=0.0, error="", failed=[])

    def pulled(self, acct: str, plat: str, videos: list, **extra) -> Path:
        d = ST.OUT / "pulled"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{acct}__{plat}.json"
        p.write_text(json.dumps(dict({"at": "2031-03-04T12:00:00+00:00", "videos": videos}, **extra)))
        return p

    # ------------------------------------------------------------------------------------------ settings, SM-3, SM-7
    def test_01_default_rates_eligible_flags_and_rules(self):
        s = SP.settings()
        self.assertEqual(s["rpm"], {"youtube": 0.05, "tiktok": 0.0, "tiktok_long": 0.35, "instagram": 0.0,
                                    "facebook": 0.30, "campaign": 1.0})
        self.assertEqual(s["goal"], 1000.0)
        for acct in ("gaming", "politics"):
            self.assertEqual(s["eligible"][acct], {"youtube": False, "tiktok": False, "facebook": False,
                                                   "instagram": False})
        self.assertEqual([r["to"] for r in s["reassign"]], ["politics", "exclude"])
        self.assertEqual(s["count_by"], "channel")                                # the page counts as before
        SP.save_settings({"count_by": "content"})
        self.assertEqual(SP.settings()["count_by"], "content")
        with self.assertRaises(SystemExit):
            SP.save_settings({"count_by": "views"})
        ok, _ = SP.save_settings({"eligible": {"politics": {"facebook": True}}, "rpm": {"campaign": 1.25}})
        self.assertTrue(ok)
        s = SP.settings()
        self.assertTrue(s["eligible"]["politics"]["facebook"])
        self.assertEqual((s["rpm"]["campaign"], len(s["reassign"])), (1.25, 2))      # the rules survive a save
        for bad in ({"eligible": {"No Body!": {"youtube": True}}}, {"eligible": {"gaming": {"youtube": "yes"}}},
                    {"eligible": {"gaming": {"snapchat": True}}}, {"eligible": []}):
            with self.assertRaises(SystemExit):
                SP.save_settings(bad)
        (ST.OUT / "settings.json").write_text(json.dumps({"reassign": [], "goal": True}))
        s = SP.settings()
        self.assertEqual((s["reassign"], s["goal"]), ([], 1000.0))                 # no rules; a bool is no goal
        (ST.OUT / "settings.json").write_text(json.dumps({"reassign": [{"to": "exclude"}, {"to": "x", "ids": ["a"]}]}))
        self.assertEqual(SP.settings()["reassign"], [{"to": "x", "ids": ["a"]}])  # a rule that fits all is dropped

    def test_02_count_by_content_moves_hearing_uploads_and_leaves_out_duplicates(self):
        aug, sep = datetime(2031, 2, 10, 18, tzinfo=UTC), datetime(2031, 3, 1, 18, tzinfo=UTC)
        (ST.OUT).mkdir(parents=True, exist_ok=True)
        (ST.OUT / "settings.json").write_text(json.dumps({"reassign": [
            {"account": "gaming", "platform": "youtube", "before": "2031-02-20", "to": "politics"},
            {"ids": ["dup1"], "to": "exclude"}, {"ids": ["k1"], "to": "keep"}, {"ids": ["z1"], "to": "nobody"}]}))
        self.pulled("gaming", "youtube_shorts", [video("hear1", aug, 900), video("gta1", sep, 50),
                                                    video("dup1", sep, 3), video("z1", sep, 4)])
        d = SP.data(self.cfg, now=datetime(2031, 3, 4, 12, tzinfo=UTC))
        got = {p["pid"]: (p["account"], p["content"]) for p in d["posts"]}
        self.assertEqual(got, {"hear1": ("gaming", "politics"), "gta1": ("gaming", "gaming"),
                               "dup1": ("gaming", ""), "z1": ("gaming", "gaming")})   # unknown target: kept
        keep_first = [{"ids": ["k1"], "to": "keep"}, {"account": "gaming", "to": "politics"}]
        k1 = {"account": "gaming", "platform": "youtube", "day": "2031-01-01", "pid": "k1"}
        self.assertEqual(SP.content_account(k1, keep_first), "gaming")           # the first rule that fits wins
        self.assertEqual(SP.content_account(dict(k1, pid="k2"), keep_first), "politics")
        self.assertIn("history", d)
        self.assertIn("followers", d)
        self.assertEqual({a["id"]: a["max"] for a in d["accounts"]}, {s: 3.0 for s in self.cfg.accounts})
        self.assertTrue({"gaming", "politics"} <= set(self.cfg.accounts))

    # ------------------------------------------------------------------------------------------ SM-4 / YTH-14
    def test_03_a_youtube_upload_made_ahead_counts_from_its_publish_time(self):
        with B.edit(self.cfg, self.gta, DAY) as bd:
            s = bd["slots"]["17:30"] if "17:30" in bd["slots"] else next(iter(bd["slots"].values()))
            k = s["slot"]
            s["status"] = B.SCHEDULED
            go = self.cfg.slot_time(DAY, k)
            s["outlets"] = {"youtube_shorts": {"video_id": "ahead1", "publish_at": go.isoformat(),
                                               "published_at": iso(go - timedelta(hours=6)),
                                               "metrics": {"views": 0, "at": iso(go - timedelta(hours=5))}}}
        early = SP.data(self.cfg, now=go - timedelta(minutes=30))
        self.assertEqual([p["pid"] for p in early["posts"]], [])                   # private until its slot
        later = SP.data(self.cfg, now=go + timedelta(minutes=1))
        p = next(p for p in later["posts"] if p["pid"] == "ahead1")
        self.assertEqual((p["hm"], p["slot"]), (k, k))
        # what YouTube says about videos that matched no clip
        up = datetime(2031, 3, 4, 17, tzinfo=UTC)
        vids = [video("sched", up), video("priv", up), video("unl", up), video("pub", up), video("rej", up)]
        ST._apply_status(vids, {"sched": {"privacy": "private", "publish_at": iso(up + timedelta(hours=6))},
                                "priv": {"privacy": "private"}, "unl": {"privacy": "unlisted"},
                                "pub": {"privacy": "public"}, "rej": {"privacy": "public", "upload": "rejected",
                                                                     "rejection": "duplicate"}})
        by = {v["platform_id"]: v for v in vids}
        self.assertEqual(by["sched"]["published_at"], iso(up + timedelta(hours=6)))
        self.assertEqual(by["sched"]["upload_at"], iso(up))
        self.assertEqual([k for k, v in by.items() if v.get("hidden")], ["priv", "unl", "rej"])
        self.pulled("gaming", "youtube_shorts", vids)
        self.assertEqual(sorted(p["pid"] for p in ST.pulled_posts(self.cfg)), ["pub", "sched"])

    # ------------------------------------------------------------------------------------------ SM-2
    def test_04_the_pulled_file_keeps_older_videos_frozen_and_reads_the_whole_channel_weekly(self):
        now = self.cfg.now()
        old, gone, new = now - timedelta(days=80), now - timedelta(days=5), now - timedelta(days=1)
        self.pulled("gaming", "youtube_shorts", [video("old", old, 941, read_at="2031-01-01T03:30:00+00:00"),
                                                    video("gone", gone, 5)], full_at=iso(now - timedelta(days=2)))
        asked = []

        def yt(acct, since):
            asked.append((acct.slug, since))
            return [video("new", new, 7)] if acct.slug == "gaming" else []
        lines = ST.refresh(self.cfg, days=60, listers={"youtube_shorts": yt})
        self.assertNotEqual(dict(asked)["gaming"], ST.EPOCH)                   # a whole read two days ago
        self.assertEqual(dict(asked)["politics"], ST.EPOCH)                 # never a whole read: now
        f = json.loads((ST.OUT / "pulled" / "gaming__youtube_shorts.json").read_text())
        by = {v["platform_id"]: v for v in f["videos"]}
        self.assertEqual(sorted(by), ["new", "old"])                              # 'gone' was in the window
        self.assertEqual((by["old"]["views"], by["old"]["frozen"], by["old"]["read_at"]),
                         (941, True, "2031-01-01T03:30:00+00:00"))
        self.assertFalse(by["new"].get("frozen"))
        self.assertTrue(any("1 older kept as last read" in ln for ln in lines), lines)
        posts = {p["pid"]: p for p in ST.pulled_posts(self.cfg)}
        self.assertTrue(posts["old"]["frozen"])
        self.assertEqual(posts["old"]["upd"], "2031-01-01T03:30:00+00:00")
        # a week later: the whole uploads list, and a video it no longer lists is gone
        asked.clear()
        f["full_at"] = iso(now - timedelta(days=8))
        (ST.OUT / "pulled" / "gaming__youtube_shorts.json").write_text(json.dumps(f))
        lines = ST.refresh(self.cfg, days=60, listers={"youtube_shorts": yt})
        self.assertEqual(dict(asked)["gaming"], ST.EPOCH)
        f = json.loads((ST.OUT / "pulled" / "gaming__youtube_shorts.json").read_text())
        self.assertEqual([v["platform_id"] for v in f["videos"]], ["new"])
        self.assertTrue(f["full_at"])
        self.assertTrue(any("(the whole channel)" in ln for ln in lines), lines)
        # through Upload-Post a TikTok missing from the read is not gone (only the newest get numbers)
        self.cfg.accounts["gaming"].outlets["tiktok"] = {"mode": "api", "via": "upload-post"}
        self.pulled("gaming", "tiktok", [video("t-in-window", gone, 9)])
        ST.refresh(self.cfg, days=60, listers={"tiktok": lambda a, since: []})
        f = json.loads((ST.OUT / "pulled" / "gaming__tiktok.json").read_text())
        self.assertEqual([(v["platform_id"], v["frozen"]) for v in f["videos"]], [("t-in-window", True)])
        # a read that fails leaves the file as it was
        before = (ST.OUT / "pulled" / "gaming__tiktok.json").read_text()

        def broken(acct, since):
            raise D.PlatformError("HTTP 500")
        self.assertTrue(any("FAILED" in ln for ln in ST.refresh(self.cfg, days=60, listers={"tiktok": broken})))
        self.assertEqual((ST.OUT / "pulled" / "gaming__tiktok.json").read_text(), before)

    def test_05_a_board_post_matched_long_ago_is_marked_frozen(self):
        with B.edit(self.cfg, self.gta, DAY) as bd:
            s = next(iter(bd["slots"].values()))
            s["status"] = B.SCHEDULED
            s["outlets"] = {"facebook_reels": {"post_id": "f1", "platform_id": "f1",
                                               "metrics": {"views": 40, "at": "2031-03-04T12:00:00+00:00"}}}
        self.pulled("gaming", "facebook_reels", [], at="2031-03-09T12:00:00+00:00")
        p = next(p for p in ST.posts_from_boards(self.cfg, days=36500) if p["pid"] == "f1")
        self.assertTrue(p["frozen"])
        self.pulled("gaming", "facebook_reels", [], at="2031-03-04T12:20:00+00:00")
        p = next(p for p in ST.posts_from_boards(self.cfg, days=36500) if p["pid"] == "f1")
        self.assertNotIn("frozen", p)
        ST.write(self.cfg, [p])                                                   # the dashboard keeps its shape
        out = json.loads((ST.OUT / "posts.json").read_text())["posts"][0]
        self.assertFalse(set(ST.PAGE_ONLY) & set(out))

    # ------------------------------------------------------------------------------------------ SM-6
    def test_06_history_snapshots_give_views_gained(self):
        t0 = datetime(2031, 3, 1, 10, 30, tzinfo=UTC)
        base = {"id": "a", "account": "gaming", "platform": "youtube", "at": iso(t0 - timedelta(days=3))}
        ST.snapshot(self.cfg, [dict(base, views=100)], now=t0)
        ST.snapshot(self.cfg, [dict(base, views=150)], now=t0 + timedelta(minutes=20))   # same hour: replaced
        day = json.loads((ST.OUT / "history" / "2031-03-01.json").read_text())
        self.assertEqual(len(day["reads"]), 1)
        self.assertEqual(day["reads"][0]["posts"]["a"][0], 150)
        self.assertEqual(day["reads"][0]["totals"], {"gaming__youtube": {"posts": 1, "views": 150}})
        t2 = t0 + timedelta(days=2)
        ST.snapshot(self.cfg, [dict(base, views=300),
                               {"id": "n", "account": "gaming", "platform": "facebook", "at": iso(t0 + timedelta(days=1)), "views": 50},
                               {"id": "o", "account": "gaming", "platform": "youtube", "at": iso(t0 - timedelta(days=30)), "views": 9000}],
                    followers={"gaming": {"youtube_shorts": {"followers": 33, "views": 92517, "at": "x", "field": "f"},
                                             "tiktok": {"error": "not readable: needs user.info.stats"}}}, now=t2)
        h = ST.history(self.cfg, now=t2)
        self.assertEqual(h["gains"], {"a": 150, "n": 50})                         # 'o' has no starting point
        self.assertEqual((h["span"], h["days"], h["first"]), (round((2 * 86400 - 1200) / 86400, 3), 2, "2031-03-01"))
        last = json.loads((ST.OUT / "history" / "2031-03-03.json").read_text())["reads"][-1]
        self.assertEqual(last["followers"], {"gaming": {"youtube_shorts": {"followers": 33, "views": 92517}}})
        (ST.OUT / "history" / "2031-03-02.json").write_text("{broken")
        self.assertEqual(ST.history(self.cfg, now=t2)["gains"], {"a": 150, "n": 50})   # a broken day is no crash
        self.assertEqual(ST.history(self.cfg, now=t2 + timedelta(days=30))["span"], 0.0)

    def test_07_update_reads_followers_and_writes_history_and_a_failure_never_stops_it(self):
        def yt(acct, since):
            return [video("v9", self.cfg.now() - timedelta(hours=3), 12)] if acct.slug == "gaming" else []
        real = ST._refresh
        with mock.patch.object(ST, "_refresh", lambda cfg, days=60: real(cfg, days=days, listers={"youtube_shorts": yt})), \
                mock.patch.object(FL, "read_all", side_effect=RuntimeError("boom")):
            lines = ST.update(self.cfg)
        self.assertTrue(any("follower counts: not read (RuntimeError)" in ln for ln in lines), lines)
        self.assertTrue((ST.OUT / "posts.json").is_file())
        hist = list((ST.OUT / "history").glob("*.json"))
        self.assertEqual(len(hist), 1)
        self.assertIn(next(p["id"] for p in ST.pulled_posts(self.cfg)), json.loads(hist[0].read_text())["reads"][0]["posts"])
        self.assertEqual(SP._failures(self.cfg, lines), [])                        # never shown as a failed read
        # nothing could be read: no snapshot of stale numbers
        shutil.rmtree(ST.OUT / "history")
        ST.update(self.cfg)                                                       # nothing connected
        self.assertFalse((ST.OUT / "history").exists())

    # ------------------------------------------------------------------------------------------ SM-8
    def test_08_follower_counts_keep_the_last_good_number_and_never_show_a_key(self):
        calls = {"n": 0}

        def yt(acct):
            calls["n"] += 1
            if calls["n"] > 2:
                raise D.PlatformError("HTTP 401 from www.googleapis.com: " + D._redact("bad token ya29.ABCDEFGHIJKLMNOP"))
            return {"followers": 33, "hidden": False, "views": 92517, "videos": 41, "field": "f"}

        def fb(acct):
            raise RuntimeError("Invalid OAuth access token EAAB1234567890abcdef")

        def ig(acct):
            raise ST.NotConnected("no Instagram account linked to the Page")
        readers = {"youtube_shorts": yt, "facebook_reels": fb, "instagram_reels": ig, "tiktok": FL.tiktok}
        got, lines = FL.read_all(self.cfg, readers)
        self.assertEqual(got["gaming"]["youtube_shorts"]["followers"], 33)
        self.assertNotIn("EAAB1234567890", json.dumps(got) + "\n".join(lines))
        self.assertIn("[a key, hidden]", got["gaming"]["facebook_reels"]["error"])
        self.assertEqual(got["gaming"]["instagram_reels"]["error"], "not connected (no Instagram account linked to the Page)")
        self.assertTrue(got["gaming"]["tiktok"]["error"].startswith("not connected"))
        got, _ = FL.read_all(self.cfg, readers)                                   # YouTube fails this time
        yt_rec = got["gaming"]["youtube_shorts"]
        self.assertEqual((yt_rec["followers"], yt_rec["views"]), (33, 92517))     # the last good numbers stay
        self.assertIn("HTTP 401", yt_rec["error"])
        self.assertNotIn("ya29.ABCDEFGHIJ", json.dumps(got))
        self.assertEqual(FL.load(), got)
        # TikTok: only with the user.info.stats scope (today's sign-ins do not have it); never a call without it
        tok = D._tok_path(self.gta, "tiktok")
        tok.parent.mkdir(parents=True, exist_ok=True)
        tok.write_text(json.dumps({"access_token": "act.x", "scope": "user.info.basic,video.list,video.publish"}))
        with mock.patch.object(D, "_http", side_effect=AssertionError("no call")):
            self.assertEqual(FL.tiktok(self.gta), {"error": FL.NEEDS_TT_STATS})
        tok.write_text(json.dumps({"access_token": "act.x", "expires_at": 4e9, "scope": "user.info.basic,user.info.stats"}))
        with mock.patch.object(D, "_http", return_value={"data": {"user": {"follower_count": 1, "likes_count": 30,
                                                                           "video_count": 4}}}) as h:
            self.assertEqual(FL.tiktok(self.gta)["followers"], 1)
        self.assertIn("/user/info/", h.call_args[0][1])
        with mock.patch.object(D, "_http", return_value={"followers_count": 12, "fan_count": 10}) as h:
            mp = D._tok_path(self.gta, "meta")
            mp.write_text(json.dumps({"page_id": "p1", "page_token": "EAAx", "ig_user_id": ""}))
            self.assertEqual(FL.facebook(self.gta)["followers"], 12)
            self.assertEqual(h.call_args[1]["params"]["fields"], "followers_count,fan_count")
            with self.assertRaises(ST.NotConnected):
                FL.instagram(self.gta)
        # a real call from a test is refused by the network gate, and that is a reason, not a crash
        yt_tok = D._tok_path(self.gta, "youtube_token")
        yt_tok.write_text(json.dumps({"scope": "https://www.googleapis.com/auth/youtube.readonly", "refresh_token": "r",
                                      "client_id": "c", "client_secret": "s"}))
        got, _ = FL.read_all(self.cfg, {"youtube_shorts": FL.youtube})
        self.assertIn("Offline", got["gaming"]["youtube_shorts"]["error"])

    # ------------------------------------------------------------------------------------------ SM-12, SM-13, SM-14
    def test_09_only_me_tiktok_tests_are_not_posts_and_by_hand_posts_keep_their_own_ids(self):
        with B.edit(self.cfg, self.politics, DAY) as bd:
            sl = list(bd["slots"].values())
            for s, pid, priv in ((sl[0], "v_pub_1", "SELF_ONLY"), (sl[1], "v_pub_2", "SELF_ONLY"),
                                 (sl[2], "v_pub_3", ""), (sl[3], "v_pub_4", "")):
                s["status"] = B.SCHEDULED
                s["outlets"] = {"tiktok": {"post_id": pid, "publish_id": pid, "platform_id": "", "privacy": priv,
                                           "url": "https://www.tiktok.com/@politics", "manual": True,
                                           "at": iso(self.cfg.slot_time(DAY, s["slot"]))}}
        posts = [p for p in ST.posts_from_boards(self.cfg, days=36500) if p["platform"] == "tiktok"]
        self.assertEqual(sorted(p["pid"] for p in posts), ["v_pub_3", "v_pub_4"])
        self.assertEqual(len({p["id"] for p in posts}), 2)                         # two posts, two ids
        self.assertEqual({p["slot"] for p in posts}, {sl[2]["slot"], sl[3]["slot"]})
        f = next(r for r in SP.data(self.cfg, now=self.cfg.slot_time(DAY, "23:59"))["factory"]
                 if r["account"] == "politics" and r["day"] == str(DAY))
        self.assertEqual((f["posted"].get("tiktok"), f["private"]), (2, {"tiktok": 2}))
        # an 'Only me' test is never matched to the public video of the same clip
        s = dict(sl[0], _key="k", publish_at=self.cfg.slot_time(DAY, sl[0]["slot"]).isoformat())
        v = video("pub1", self.cfg.slot_time(DAY, sl[0]["slot"]), text=s.get("slug", ""))
        self.assertEqual(ST.match([s], "tiktok", [dict(v, platform_id="v_pub_1")]), {})

    def test_10_old_slot_times_keep_their_slot(self):
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["13:00"] = {"slot": "13:00", "publish_at": self.cfg.slot_time(DAY, "13:00").isoformat(),
                                    "status": B.SCHEDULED, "notes": [], "host": "Senate Committee on the Judiciary",
                                    "rights": "owned",
                                    "outlets": {"facebook_reels": {"post_id": "f13", "at": iso(self.cfg.slot_time(DAY, "15:13"))}}}
        p = next(p for p in SP.data(self.cfg, now=self.cfg.slot_time(DAY, "23:00"))["posts"] if p["pid"] == "f13")
        self.assertEqual((p["slot"], p["host"], p["rights"]), ("13:00", "Senate Committee on the Judiciary", "owned"))

    # ------------------------------------------------------------------------------------------ SM-18
    def test_11_collect_copies_the_jobs_publisher_and_rights(self):
        ad = self.politics.account_dir
        (ad / "Publish").mkdir(parents=True)
        (ad / "Jobs" / "SJC FBI 09.15.31").mkdir(parents=True)
        (ad / "Jobs" / "SJC FBI 09.15.31" / "project.json").write_text(json.dumps({
            "source_url": "https://www.judiciary.senate.gov/hearing", "source_channel": "Senate Committee on the Judiciary",
            "rights": "owned"}))
        (ad / "Publish" / "schedule.json").write_text(json.dumps({
            "_how": "notes", str(DAY): {"05:00": {"job": "SJC FBI 09.15.31", "slug": "patel-yes-or-no"}}}))
        folder = self.tmp / "delivered" / "0500 patel-yes-or-no"
        folder.mkdir(parents=True)
        self.assertEqual(K.source_of(self.politics, folder, str(DAY)),
                         {"host": "Senate Committee on the Judiciary", "rights": "owned"})
        self.assertEqual(K.source_of(self.politics, folder, "2031-01-01"),               # another day still finds it
                         {"host": "Senate Committee on the Judiciary", "rights": "owned"})
        (folder / "source.json").write_text(json.dumps({"publisher": "C-SPAN", "rights": "rebroadcast"}))
        self.assertEqual(K.source_of(self.politics, folder, str(DAY)), {"host": "C-SPAN", "rights": "rebroadcast"})
        self.assertEqual(K.source_of(self.politics, self.tmp / "delivered" / "0700 nothing-known", str(DAY)), {})
        # through collect: a delivered clip lands on the board with its source
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg is needed to make a clip")
        (folder / "source.json").unlink()
        root = self.politics.delivered_dirs(DAY)[0]
        root.mkdir(parents=True)
        d = root / folder.name
        shutil.move(str(folder), str(d))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=108x192:d=2",
                        "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "2", "-c:v", "libx264", "-preset",
                        "ultrafast", "-c:a", "aac", "-shortest", str(d / "01-patel-yes-or-no.mp4")], check=True)
        for f in ("youtube.txt", "tiktok.txt", "facebook.txt", "instagram.txt"):
            (d / f).write_text("Patel | Senate Judiciary\n\nWords.\n")
        K.collect(self.cfg, self.politics, DAY)
        s = next(s for s in B.read(self.politics, DAY)["slots"].values() if s.get("slug") == "patel-yes-or-no")
        self.assertEqual((s["host"], s["rights"]), ("Senate Committee on the Judiciary", "owned"))

    # ------------------------------------------------------------------------------------------ the page
    def test_12_the_page_shows_two_money_figures_and_the_count_switch(self):
        page = SP.page("tok", "", "")
        for words in ("Earned so far (actual)", "If monetized (estimate)", "Run-rate a month (estimate)", "Goal gap",
                      "Count by", "Paid programs", "Accounts side by side", "What it takes", "In it",
                      "only clips over 60s pay", "Invite only", "first week of history".capitalize()):
            self.assertIn(words, page)
        self.assertNotIn("TikTok is posted by hand, so it has no numbers", SP.__doc__)
        self.assertNotIn("Est. earnings", page)


if __name__ == "__main__":
    unittest.main()
