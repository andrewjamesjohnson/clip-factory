import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"]="1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())
"""How long people watch (2026-10-19): the watch fields read beside the counts on Instagram, Facebook and YouTube,
kept on each post, carried between reads, worked out where a platform gives only seconds, and shown on the Stats tab.
Fake platforms and throwaway folders only: every call goes to a fake _http, and nothing can reach the network."""
import json
import shutil
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B          # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import net                 # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import stats as ST         # noqa: E402
from clipfactory import statspage as SP     # noqa: E402

UTC = timezone.utc
NOW = datetime(2031, 3, 20, 12, tzinfo=UTC)


def refused() -> D.PlatformError:
    """What Meta says when one name in the list is not there (2026-10-20, both Pages): it never says which."""
    return D.PlatformError("HTTP 400 from graph.facebook.com: (#100) The value must be a valid insights metric")


class WatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-watch-"))
        cls.old = (C.STATE, ST.OUT, O.TOKENS, D.TOKENS, D._http, net.guard)
        C.STATE = cls.tmp / "state"
        ST.OUT = C.STATE / "stats"
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["review_port"] = 0
        for slug, a in raw["accounts"].items():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["account_dir"] = str(cls.tmp / "acct" / slug)
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))

    @classmethod
    def tearDownClass(cls):
        C.STATE, ST.OUT, O.TOKENS, D.TOKENS, D._http, net.guard = cls.old
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        for d in ("state", "keys", "drafts", "delivered", "acct"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.gta = self.cfg.account("gaming")
        self.calls = []
        D.NOTES.clear()
        D._http = self.refuse
        SP._cache["key"] = None
        D._save(self.gta, "meta", {"page_id": "P1", "page_token": "PT", "ig_user_id": "IG1"})

    def tearDown(self):
        D._http = self.refuse
        net.guard = self.old[5]

    def no_refresh(self) -> None:
        """The fake token never expires, so no refresh is made; the gate in front of a refresh is let through here only."""
        net.guard = lambda what: None

    def refuse(self, *a, **kw):
        raise AssertionError(f"unexpected call {a}")

    def fake(self, answer):
        """_http that records each call as (url, metric list) and asks answer(url, params) for the reply."""
        def http(method, url, params=None, form=None, body=None, data=None, headers=None, timeout=0):
            self.assertEqual(method, "GET")                 # a read never posts anything
            self.calls.append((url, (params or {}).get("metric") or (params or {}).get("metrics") or ""))
            return answer(url, params or {})
        D._http = http

    # ------------------------------------------------------------------------------------------------ Instagram
    def ig_media(self, *ids, kind="REELS"):
        return {"data": [{"id": i, "caption": f"clip {i}", "media_type": "VIDEO", "media_product_type": kind,
                          "permalink": f"https://www.instagram.com/reel/{i}/", "timestamp": "2031-03-18T15:00:00+0000",
                          "like_count": 4, "comments_count": 2} for i in ids]}

    @staticmethod
    def rows(**vals):
        return {"data": [{"name": k, "period": "lifetime", "values": [{"value": v}]} for k, v in vals.items()]}

    def test_01_instagram_reads_watch_time_reach_saves_and_skip_rate(self):
        def answer(url, p):
            if url.endswith("/IG1/media"):
                return self.ig_media("m1")
            return self.rows(views=900, likes=40, comments=3, shares=7, reach=610, saved=12,
                             ig_reels_avg_watch_time=8350, ig_reels_video_view_total_time=7515000, reels_skip_rate=41.26)
        self.fake(answer)
        v = D.ig_list(self.gta, NOW - timedelta(days=7))[0]
        self.assertEqual((v["views"], v["likes"], v["comments"], v["shares"]), (900, 40, 3, 7))
        self.assertEqual((v["watch_s"], v["watch_total_s"], v["reach"], v["saved"], v["skip_pct"]),
                         (8.3, 7515, 610, 12, 41.3))
        self.assertEqual(self.calls[1][1].split(","), list(D.IG_COUNTS + D.IG_WATCH))   # one call asks for all of it
        self.assertEqual(len(self.calls), 2)

    def test_02_a_refused_metric_is_found_dropped_for_the_read_and_the_views_stay(self):
        def answer(url, p):
            if url.endswith("/IG1/media"):
                return self.ig_media("m1", "m2", "m3")
            if "reels_skip_rate" in p["metric"]:
                raise refused()
            return self.rows(views=50, reach=40, ig_reels_avg_watch_time=3000)
        self.fake(answer)
        got = D.ig_list(self.gta, NOW - timedelta(days=7))
        self.assertEqual([(v["views"], v["watch_s"], v["reach"], "skip_pct" in v) for v in got], [(50, 3.0, 40, False)] * 3)
        asked = [m for u, m in self.calls if u.endswith("/insights")]
        # m1: all (refused), each of the 5 watch metrics alone (only the skip rate refused), all but the skip rate;
        # m2 and m3: all but the skip rate, once each
        self.assertEqual(len(asked), 1 + 5 + 1 + 2)
        self.assertEqual(sum("reels_skip_rate" in m for m in asked), 2)
        self.assertEqual(asked[1:6], list(D.IG_WATCH))
        self.assertIn("refused reels_skip_rate", D.NOTES["gaming__instagram_reels"][0])

    def test_03_any_other_no_falls_back_to_the_counts_alone_and_then_to_the_media_fields(self):
        def answer(url, p):
            if url.endswith("/IG1/media"):
                return self.ig_media("m1", "m2")
            if url.endswith("/m2/insights"):
                raise D.PlatformError("HTTP 400 from graph.facebook.com: (#10) Not enough viewers")
            if "reach" in p["metric"]:
                raise D.PlatformError("HTTP 500 from graph.facebook.com: An unknown error occurred")
            return self.rows(views=77, likes=5)
        self.fake(answer)
        got = {v["platform_id"]: v for v in D.ig_list(self.gta, NOW - timedelta(days=7))}
        self.assertEqual((got["m1"]["views"], got["m1"]["likes"]), (77, 5))
        self.assertFalse(any(k in got["m1"] for k in ST.WATCH))
        self.assertEqual((got["m2"]["views"], got["m2"]["likes"], got["m2"]["comments"]), (0, 4, 2))   # media fields
        self.assertEqual([m.count(",") for u, m in self.calls if u.endswith("/insights")], [8, 3, 8, 3])

    def test_04_an_old_feed_video_is_asked_for_its_counts_only(self):
        def answer(url, p):
            if url.endswith("/IG1/media"):
                return self.ig_media("f1", kind="FEED")
            self.assertEqual(p["metric"], ",".join(D.IG_COUNTS))
            return self.rows(views=5)
        self.fake(answer)
        self.assertEqual(D.ig_list(self.gta, NOW - timedelta(days=7))[0]["views"], 5)

    # ------------------------------------------------------------------------------------------------ Facebook
    def fb_reels(self, *ids):
        return {"data": [{"id": i, "description": f"clip {i}", "created_time": "2031-03-18T15:00:00+0000",
                          "permalink_url": f"/reel/{i}", "length": 40.0,
                          "comments": {"summary": {"total_count": 3}}} for i in ids]}

    def test_05_facebook_reads_average_watch_plays_replays_and_the_retention_curve(self):
        graph = {str(k): round(1 - k / 50, 3) for k in range(41)}                 # 1.0 at the start, 0.2 at the end
        def answer(url, p):
            if url.endswith("/P1/video_reels"):
                return self.fb_reels("r1")
            self.assertEqual(p["period"], "lifetime")
            return self.rows(blue_reels_play_count=500, post_video_likes_by_reaction_type={"LIKE": 9, "LOVE": 1},
                             post_video_social_actions={"COMMENT": 3, "SHARE": 4}, post_video_avg_time_watched=12400,
                             post_video_view_time=6200000, fb_reels_total_plays=560, fb_reels_replay_count=60,
                             post_video_retention_graph=graph)
        self.fake(answer)
        v = D.fb_list(self.gta, NOW - timedelta(days=7))[0]
        self.assertEqual((v["views"], v["likes"], v["shares"], v["comments"], v["dur"]), (500, 10, 4, 3, 40.0))
        self.assertEqual((v["watch_s"], v["watch_total_s"], v["plays_total"], v["replays"], "reach" in v),
                         (12.4, 6200, 560, 60, False))
        self.assertNotIn("post_impressions_unique", self.calls[1][1])       # refused on Reels (both Pages, 10-20)
        self.assertEqual(len(v["curve"]), 21)
        self.assertEqual((v["curve"][0], v["curve"][10], v["curve"][20]), (1.0, 0.6, 0.2))
        w = ST.watch(v, "facebook_reels", 40.0)
        self.assertEqual(w["watch_pct"], 31.0)                                   # 12.4 s of 40 s
        self.assertEqual(w["left_3s_pct"], 6.0)                                  # 3 s of 40 = 7.5% of the clip

    def test_06_facebook_without_the_curve_still_reads_the_watch_time(self):
        def answer(url, p):
            if url.endswith("/P1/video_reels"):
                return self.fb_reels("r1", "r2")
            if "post_video_retention_graph" in p["metric"]:
                raise refused()
            return self.rows(blue_reels_play_count=20, post_video_avg_time_watched=2500)
        self.fake(answer)
        got = D.fb_list(self.gta, NOW - timedelta(days=7))
        self.assertEqual([(v["views"], v["watch_s"], "curve" in v) for v in got], [(20, 2.5, False)] * 2)
        self.assertEqual(sum("retention" in m for u, m in self.calls), 2)        # the batch, then alone: never again
        self.assertIn("refused post_video_retention_graph", D.NOTES["gaming__facebook_reels"][0])

    def test_06b_a_refusal_no_single_metric_explains_reads_the_counts_and_asks_each_alone_once_a_read(self):
        def answer(url, p):
            if url.endswith("/P1/video_reels"):
                return self.fb_reels("r1", "r2")
            if "," in p["metric"] and "post_video_view_time" in p["metric"]:
                raise refused()                      # together no, each alone yes: nothing to drop
            return self.rows(blue_reels_play_count=33)
        self.fake(answer)
        got = D.fb_list(self.gta, NOW - timedelta(days=7))
        self.assertEqual([v["views"] for v in got], [33, 33])
        asked = [m for u, m in self.calls if u.endswith("/video_insights")]
        self.assertEqual(len(asked), (1 + 5 + 1) + (1 + 1))                     # r2 is not probed again
        self.assertNotIn("gaming__facebook_reels", D.NOTES)

    # ------------------------------------------------------------------------------------------------ YouTube
    def yt_token(self, analytics: bool) -> None:
        tf = O.yt_token_file(self.gta)
        tf.parent.mkdir(parents=True, exist_ok=True)
        scope = " ".join(O.YT_SCOPES if analytics else O.YT_SCOPES[:2])
        tf.write_text(json.dumps({"access_token": "AT", "expires_at": 9e12, "client_id": "c", "client_secret": "s",
                                  "refresh_token": "r", "scope": scope}))

    def yt_answer(self, vids: dict, bad_list: bool = False, curve_fails: bool = False):
        """vids: id -> (went up, views). The Data API lists them; Analytics answers for each."""
        def answer(url, p):
            if url.endswith("/channels"):
                return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
            if url.endswith("/playlistItems"):
                return {"items": [{"contentDetails": {"videoId": i, "videoPublishedAt": at.isoformat()}}
                                  for i, (at, _) in vids.items()]}
            if url.endswith("/videos"):
                return {"items": [{"id": i, "snippet": {"title": i, "publishedAt": at.isoformat()},
                                   "statistics": {"viewCount": str(n)}, "contentDetails": {"duration": "PT40S"}}
                                  for i, (at, n) in vids.items()]}
            self.assertEqual(url, D.YTA)
            self.assertEqual(p["ids"], "channel==MINE")
            if p.get("dimensions") == "elapsedVideoTimeRatio":
                if curve_fails:
                    raise D.PlatformError("HTTP 403 from youtubeanalytics.googleapis.com: Forbidden")
                return {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}],
                        "rows": [[r / 100, 1.2 - r / 200] for r in range(1, 101)]}
            heads = ["video", "views", "engagedViews", "averageViewDuration", "averageViewPercentage",
                     "estimatedMinutesWatched"]
            if p.get("dimensions") == "video":
                if bad_list:
                    raise D.PlatformError("HTTP 400 from youtubeanalytics.googleapis.com: Invalid filter")
                ids = p["filters"].split("==", 1)[1].split(",")
            else:
                ids = [p["filters"].split("==", 1)[1]]
                heads = heads[1:]
            rows = [[i, vids[i][1], vids[i][1] // 4, 6.5, 16.25, 3.5][(0 if "video" in heads else 1):] for i in ids]
            return {"columnHeaders": [{"name": h} for h in heads], "rows": rows}
        return answer

    def test_07_youtube_without_the_analytics_sign_in_reads_as_before_and_says_so(self):
        self.yt_token(analytics=False)
        self.no_refresh()
        self.fake(self.yt_answer({"a1": (NOW - timedelta(days=3), 80)}))
        v = D.yt_list(self.gta, NOW - timedelta(days=7))[0]
        self.assertEqual(v["views"], 80)
        self.assertFalse(any(k in v for k in ST.WATCH))
        self.assertFalse(any(u == D.YTA for u, _ in self.calls))
        self.assertIn("connect youtube gaming", D.NOTES["gaming__youtube_shorts"][0])

    def test_08_youtube_analytics_and_the_retention_curve_read_once_a_day(self):
        self.yt_token(analytics=True)
        self.no_refresh()
        vids = {"new": (NOW - timedelta(days=1), 10), "mid": (NOW - timedelta(days=5), 400),
                "old": (NOW - timedelta(days=40), 9000)}
        self.fake(self.yt_answer(vids))
        got = {}
        for v in D.yt_list(self.gta, NOW - timedelta(days=60)):
            got[v["platform_id"]] = v
        # yt_list dates the read with the real clock; read the watch part again at a fixed one to pin the curves
        self.calls.clear()
        shutil.rmtree(ST.OUT / "curves", ignore_errors=True)
        rows = [dict(got[i], **{k: None for k in ST.WATCH}) for i in got]
        rows = [{k: v for k, v in r.items() if v is not None} for r in rows]
        D.yt_watch(self.gta, {"Authorization": "Bearer AT"}, rows, now=NOW)
        by = {r["platform_id"]: r for r in rows}
        self.assertEqual((by["mid"]["engaged"], by["mid"]["engaged_pct"], by["mid"]["watch_s"], by["mid"]["watch_pct"],
                          by["mid"]["watch_total_s"]), (100, 25.0, 6.5, 16.2, 210))
        curves = [u for u, m in self.calls if m == "audienceWatchRatio"]
        self.assertEqual(len(curves), 1)                     # 'new' is too new, 'old' past CURVE_DAYS
        self.assertEqual((by["mid"]["curve"][0], by["mid"]["curve"][20]), (1.195, 0.7))
        self.assertNotIn("curve", by["new"])
        # the next read inside CURVE_EVERY keeps the kept curve and asks for none
        self.calls.clear()
        again = [{k: v for k, v in r.items() if k != "curve"} for r in rows]
        D.yt_watch(self.gta, {"Authorization": "Bearer AT"}, again, now=NOW + timedelta(hours=3))
        self.assertEqual([m for u, m in self.calls if m == "audienceWatchRatio"], [])
        self.assertEqual(next(r for r in again if r["platform_id"] == "mid")["curve"][20], 0.7)
        w = ST.watch(by["mid"], "youtube_shorts", 40)
        self.assertEqual((w["watch_pct"], w["left_3s_pct"]), (16.2, 2.7))       # YouTube's own %, kept as is

    def test_09_a_list_youtube_refuses_is_read_one_video_at_a_time_and_a_failed_curve_stops_the_curves(self):
        vids = {f"v{i}": (NOW - timedelta(days=3 + i), 100 + i) for i in range(3)}
        self.fake(self.yt_answer(vids, bad_list=True, curve_fails=True))
        rows = [{"platform_id": i, "published_at": at.isoformat(), "views": n, "dur": 40} for i, (at, n) in vids.items()]
        D.yt_watch(self.gta, {"Authorization": "Bearer AT"}, rows, now=NOW)
        self.assertEqual([r.get("engaged") for r in rows], [25, 25, 25])
        self.assertEqual(sum(1 for u, m in self.calls if m == "audienceWatchRatio"), 1)
        said = " ".join(D.NOTES["gaming__youtube_shorts"])
        self.assertIn("one at a time", said)
        self.assertIn("retention curve not read", said)

    def test_10_a_missing_youtube_analytics_permission_is_said_at_connect(self):
        self.assertIn("https://www.googleapis.com/auth/yt-analytics.readonly", O.YT_SCOPES)
        self.assertFalse(any("youtube.force-ssl" in s or s.endswith("/youtube") for s in O.YT_SCOPES))   # never edits
        old = (O.yt_channel, O.yt_refusal)
        try:
            O.yt_channel = lambda tok: {"id": "UC1", "title": "Gaming", "handle": "@gaming"}
            O.yt_refusal = lambda cfg, acct, ch: ""
            tok = {"access_token": "AT", "refresh_token": "r", "expires_in": 3600}
            said = O.save_youtube(None, self.gta, {"client_id": "c", "client_secret": "s"},
                                  dict(tok, scope=" ".join(O.YT_SCOPES[:2])))
            self.assertIn("No watch time", said)
            said = O.save_youtube(None, self.gta, {"client_id": "c", "client_secret": "s"},
                                  dict(tok, scope=" ".join(O.YT_SCOPES)))
            self.assertNotIn("No watch time", said)
        finally:
            O.yt_channel, O.yt_refusal = old

    # ------------------------------------------------------------------------------------------------ stats.py
    def test_11_curve_points(self):
        self.assertEqual(D.curve([]), [])
        c = D.curve([(r / 100, r) for r in range(1, 101)])
        self.assertEqual((c[0], c[1], c[10], c[20]), (1, 5, 50, 100))            # first point stands for 0
        self.assertEqual(D.curve([(0.5, 0.4), (0, 1.0), ("x", 2)]), [1.0] * 10 + [0.4] * 11)

    def test_12_watch_fields_carry_forward_and_are_worked_out_from_the_length_sent(self):
        ig = {"watch_s": 9.0, "skip_pct": 40.0}
        self.assertEqual(ST.watch(ig, "instagram_reels", 45.0), {"watch_s": 9.0, "skip_pct": 40.0, "watch_pct": 20.0})
        self.assertEqual(ST.watch(ig, "instagram_reels", 0), ig)                 # no length known: no %
        old = {"watch_s": 4.0, "watch_pct": 10.0, "skip_pct": 50.0, "reach": 9, "views": 3}
        self.assertEqual(ST.watch({"watch_s": 9.0}, "instagram_reels", 45.0, old),
                         {"watch_s": 9.0, "watch_pct": 20.0, "skip_pct": 50.0, "reach": 9})
        self.assertEqual(ST.watch({}, "youtube_shorts", 40.0, {"watch_pct": 88.0}), {"watch_pct": 88.0})
        flat = [1.0] * 21
        self.assertNotIn("left_3s_pct", ST.watch({"curve": flat}, "facebook_reels", 5.0))    # too short to say
        self.assertEqual(ST.watch({"curve": flat}, "facebook_reels", 30.0)["left_3s_pct"], 0.0)
        self.assertEqual(ST.watch({"watch_s": "x"}, "facebook_reels", 30.0), {"watch_s": "x"})   # never a crash
        # nobody watched it: no averages (Facebook says 0 s), even ones a read before had; the totals stay
        self.assertEqual(ST.watch({"views": 0, "watch_s": 0.0, "watch_total_s": 0}, "facebook_reels", 30.0,
                                  {"watch_pct": 12.0, "skip_pct": 40.0, "reach": 3}), {"watch_total_s": 0, "reach": 3})

    def gta_board(self):
        """One scheduled GTA slot: a 40-second clip, its own 30-second Instagram cut, out on Instagram and Facebook."""
        day = NOW.date()
        with B.edit(self.cfg, self.gta, day) as bd:
            s = next(iter(bd["slots"].values()))
            s.update(status=B.SCHEDULED, slug="the-clip", clip={"path": "/x.mp4", "seconds": 40.0},
                     cuts={"instagram_reels": {"path": "/i.mp4", "seconds": 30.0}})
            s["outlets"] = {"instagram_reels": {"post_id": "m1", "platform_id": "m1", "at": NOW.isoformat()},
                            "facebook_reels": {"post_id": "r1", "platform_id": "r1", "at": NOW.isoformat()}}
            return day, s["slot"]

    def test_13_a_read_keeps_the_fields_on_the_board_posts_json_has_them_the_dashboard_files_do_not(self):
        day, slot = self.gta_board()
        at = self.cfg.slot_time(day, slot).isoformat()
        ig = [{"platform_id": "m1", "url": "u", "text": "t", "published_at": at, "dur": 0, "views": 100,
               "watch_s": 6.0, "skip_pct": 45.5, "reach": 80, "saved": 2},
              {"platform_id": "m9", "url": "u9", "text": "not a clip", "published_at": at, "dur": 0, "views": 7,
               "watch_s": 2.0}]
        fb = [{"platform_id": "r1", "url": "u", "text": "t", "published_at": at, "dur": 40.0, "views": 60,
               "watch_s": 10.0, "replays": 4, "curve": [1.0] * 10 + [0.5] * 11}]

        def listers(ig_rows, fb_rows):
            def one(rows, plat):
                def lister(acct, since):
                    if acct.slug != "gaming":
                        return []
                    D.note(acct, plat, "a note from the read")
                    return [dict(r) for r in rows]
                return lister
            return {"instagram_reels": one(ig_rows, "instagram_reels"), "facebook_reels": one(fb_rows, "facebook_reels")}

        lines = ST.refresh(self.cfg, listers=listers(ig, fb), full=False)
        self.assertIn("watch time on 2", " ".join(lines))
        self.assertIn("a note from the read", " ".join(lines))
        bd = B.read(self.gta, day)
        m = bd["slots"][slot]["outlets"]["instagram_reels"]["metrics"]
        self.assertEqual((m["views"], m["watch_s"], m["watch_pct"], m["skip_pct"]), (100, 6.0, 20.0, 45.5))   # 6 of 30 s
        f = bd["slots"][slot]["outlets"]["facebook_reels"]["metrics"]
        self.assertEqual((f["watch_pct"], f["left_3s_pct"], f["replays"]), (25.0, 0.0, 4))
        # the next read brings the counts but no watch time: the last watch numbers stay
        bare = [{k: v for k, v in r.items() if k not in ST.WATCH} for r in ig]
        ST.refresh(self.cfg, listers=listers([dict(r, views=150) for r in bare], fb), full=False)
        m = B.read(self.gta, day)["slots"][slot]["outlets"]["instagram_reels"]["metrics"]
        self.assertEqual((m["views"], m["watch_s"], m["skip_pct"]), (150, 6.0, 45.5))
        pulled = json.loads((ST.OUT / "pulled" / "gaming__instagram_reels.json").read_text())["videos"]
        self.assertEqual([(v["platform_id"], v["watch_s"]) for v in pulled], [("m9", 2.0)])
        posts = {(p["platform"], p["pid"]): p for p in ST.all_posts(self.cfg, days=30)}
        self.assertEqual(posts[("instagram", "m1")]["skip_pct"], 45.5)
        self.assertEqual(posts[("instagram", "m9")]["watch_s"], 2.0)
        self.assertEqual(len(posts[("facebook", "r1")]["curve"]), 21)
        ST.write(self.cfg, list(posts.values()))
        flat = json.loads((ST.OUT / "posts.json").read_text())["posts"]
        self.assertEqual({p["pid"]: p.get("watch_s") for p in flat}, {"m1": 6.0, "m9": 2.0, "r1": 10.0})
        for f in (ST.OUT / "dashboard" / "posts").glob("*.json"):
            for item in json.loads(f.read_text())["items"].values():
                self.assertFalse(set(item) & set(ST.WATCH), item)                 # the dashboard's shape
        # a post with no watch fields still counts as before, and the history keeps its five numbers
        snap = ST.snapshot(self.cfg, list(posts.values()), now=NOW)
        self.assertTrue(all(len(v) == 5 for v in json.loads(snap.read_text())["reads"][0]["posts"].values()))

    def test_14_the_stats_tab_gets_only_the_fields_a_post_has_and_names_youtube_without_watch_time(self):
        day, slot = self.gta_board()
        with B.edit(self.cfg, self.gta, day) as bd:
            bd["slots"][slot]["outlets"]["instagram_reels"]["metrics"] = {
                "views": 10, "watch_pct": 22.5, "skip_pct": 30.0, "curve": [1.0] * 21, "at": NOW.isoformat()}
        self.yt_token(analytics=False)
        d = SP.data(self.cfg, now=NOW + timedelta(days=1))
        ps = {p["platform"]: p for p in d["posts"]}
        self.assertEqual((ps["instagram"]["watch_pct"], ps["instagram"]["skip_pct"]), (22.5, 30.0))
        self.assertNotIn("curve", ps["instagram"])                               # not sent to the page
        self.assertNotIn("watch_pct", ps["facebook"])                            # no nulls for what it lacks
        self.assertEqual(d["no_watch"], ["Gaming"])
        self.yt_token(analytics=True)
        SP._cache["key"] = None
        self.assertEqual(SP.data(self.cfg, now=NOW + timedelta(days=1))["no_watch"], [])
        html = SP.page("tok", "", "")
        for bit in ('id="wtime"', 'id="f-wby"', "renderWatch(cur)", "watched_pct"):
            self.assertIn(bit, html)


if __name__ == "__main__":
    unittest.main()
