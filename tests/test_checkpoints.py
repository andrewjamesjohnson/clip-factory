import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Checkpoint stats (2026-10-20): each posted clip re-read at 1h, 3h, 6h, 24h, 72h, 7d and 30d, a series per post keyed
# by the platform's id, early winners and losers against the account's usual, the extra metrics in calls of their
# own, YouTube Analytics only with its permission, the holds obeyed, the nightly read's lock never touched, the backfill
# from its snapshots, and the Stats tab's /stats/series. Throwaway folders, fake platforms, a hand-moved clock.

import builtins
import contextlib
import io
import json
import shutil
import statistics
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import (Clock, FakeGraph, FakeYouTube, cfg_in_tmp, meta_error, patch_http, serve,  # noqa: E402
                          write_tokens)

from clipfactory import board as B              # noqa: E402
from clipfactory import capabilities as CAP     # noqa: E402
from clipfactory import checkpoints as CP       # noqa: E402
from clipfactory import checkpoints_api as API  # noqa: E402
from clipfactory import config as C             # noqa: E402
from clipfactory import outlets as O            # noqa: E402
from clipfactory import outward as OW           # noqa: E402
from clipfactory import registry as REG         # noqa: E402
from clipfactory import social as SOC           # noqa: E402
from clipfactory import stats as ST             # noqa: E402
from clipfactory import statspage as SP         # noqa: E402
from clipfactory import switches as SW          # noqa: E402

DAY = date(2031, 3, 4)
ALL = ("facebook_reels", "instagram_reels", "youtube_shorts")


def ig_answer(views: int = 100, extra=None):
    """Instagram insights: each asked metric with a value (views as given, the rest small)."""
    def f(call):
        ms = call["params"]["metric"].split(",")
        if extra is not None and ms != list(API.IG_BASE):
            return extra(ms)
        return {"data": [{"name": m, "period": "lifetime", "values": [{"value": views if m == "views" else 5}]}
                         for m in ms]}
    return f


def fb_answer(views: int = 200, extra=None):
    def f(call):
        ms = call["params"]["metric"].split(",")
        if ms != list(API.FB_BASE):
            if extra is not None:
                return extra(ms)
            return {"data": [{"name": m, "values": [{"value": 1000 if "time" in m else 7}]} for m in ms]}
        return {"data": [{"name": "blue_reels_play_count", "values": [{"value": views}]},
                         {"name": "post_video_likes_by_reaction_type", "values": [{"value": {"like": 3, "love": 1}}]},
                         {"name": "post_video_social_actions", "values": [{"value": {"COMMENT": 2, "SHARE": 1}}]}]}
    return f


def yt_answer(views: int = 500, private=(), gone=()):
    def f(call):
        ids = call["params"]["id"].split(",")
        return {"items": [{"id": i, "statistics": {"viewCount": str(views), "likeCount": "5", "commentCount": "1"},
                           "status": {"privacyStatus": "private" if i in private else "public"}}
                          for i in ids if i not in gone]}
    return f


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.led = self.cfg.account("politics")
        for a in (self.gta, self.led):
            write_tokens(a)
        self.clock = Clock(datetime(2031, 3, 4, 7, 0, tzinfo=self.cfg.tz())).patch(self, self.cfg)
        p = mock.patch.object(CP, "SLEEP", lambda s: None)
        p.start()
        self.addCleanup(p.stop)
        CP._CACHE.clear()
        self.g, self.yt = FakeGraph(), FakeYouTube()
        patch_http(self, graph=self.g, yt=self.yt)

    def on(self, acct, *keys) -> None:
        for k in keys or ("checkpoints",):
            SW.set_mode(self.cfg, acct, k, "on", "test")

    def put(self, acct, day: date, slot: str, outlets=ALL, tag: str = "", metrics=None) -> dict:
        """A posted slot on the board: Facebook and Instagram with their ids (published at the slot), YouTube with
        its video id and publish time. {outlet: id}."""
        when = self.cfg.slot_time(day, slot)
        at = when.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        ids = {}
        with B.edit(self.cfg, acct, day) as bd:
            s = bd["slots"].setdefault(slot, {"slot": slot, "publish_at": when.isoformat(), "notes": []})
            s.update(status=B.SCHEDULED, slug=f"clip-{slot.replace(':', '')}{tag}", format="room",
                     clip={"seconds": 40.0}, source_title=f"Clip at {slot}{tag}")
            for o in outlets:
                pid = (f"yt{day:%m%d}{slot.replace(':', '')}{tag}" if o == "youtube_shorts"
                       else f"{1 + ALL.index(o)}{day:%y%m%d}{slot.replace(':', '')}{len(tag)}")
                rec = ({"video_id": pid, "url": f"https://youtube.com/shorts/{pid}", "publish_at": when.isoformat(),
                        "at": at} if o == "youtube_shorts" else
                       {"post_id": pid, "platform_id": pid, "url": "https://x", "published_at": at, "at": at,
                        "provider": "direct"})
                if metrics is not None:
                    rec["metrics"] = dict(metrics)
                s.setdefault("outlets", {})[o] = rec
                ids[o] = pid
        return ids

    def run_at(self, t: datetime):
        self.clock.set(t)
        CP._CACHE.clear()
        return CP.run(self.cfg, now=t, quiet=False)

    def entry(self, acct, outlet, pid):
        CP._CACHE.clear()
        return CP.series(acct.slug, outlet, pid)

    def meta_routes(self, ig=None, fb=None):
        self.g.on("GET", r"/\d+/insights$", ig or ig_answer())
        self.g.on("GET", r"/\d+/video_insights$", fb or fb_answer())

    def went(self, day=DAY, slot="07:00"):
        return self.cfg.slot_time(day, slot)


# ------------------------------------------------------------------------------------------------------ the clock
class DueTest(Base):
    def test_1_jitter_is_fixed_per_post_and_stays_in_bounds(self):
        seen = set()
        for i in range(300):
            pid = f"p{i}"
            for lbl, off in CP.LABELS:
                j = CP.jitter_min(pid, off)
                self.assertEqual(j, CP.jitter_min(pid, off))
                self.assertGreaterEqual(j, 2.0)
                self.assertLessEqual(j, 360.0)
                if off * 0.05 >= 2 and off * 0.15 <= 360:
                    self.assertGreaterEqual(j, off * 0.05 - 1e-9)
                    self.assertLessEqual(j, off * 0.15 + 1e-9)
            seen.add(round(CP.jitter_min(pid, 1440), 3))
        self.assertGreater(len(seen), 250)          # spread: ten posts of one slot are not asked in one minute

    def test_2_due_then_several_due_is_one_read_and_the_rest_missed(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=50))
        went = self.went()
        self.assertEqual(self.run_at(went + timedelta(minutes=30))[:1], ["Gaming: 0 checkpoint reads"])
        self.assertEqual(self.g.calls, [])
        self.run_at(went + timedelta(minutes=80))
        self.assertEqual(len(self.g.calls), 1)
        self.assertEqual(self.g.calls[0]["params"]["metric"], ",".join(API.FB_BASE))
        e = self.entry(self.gta, "facebook_reels", ids["facebook_reels"])
        self.assertEqual([(p["label"], p["age_min"], p["views"], p["src"]) for p in e["points"]],
                         [("1h", 80, 50, "insights")])
        self.assertEqual(e["points"][0]["likes"], 4)
        self.assertEqual((e["points"][0]["comments"], e["points"][0]["shares"]), (2, 1))
        # the same label is never read twice
        self.run_at(went + timedelta(minutes=95))
        self.assertEqual(len(self.g.calls), 1)
        # the Mac slept three days: one read under 72h with its true age, 3h 6h 24h missed
        t = went + timedelta(hours=78, minutes=10)
        self.run_at(t)
        self.assertEqual(len(self.g.calls), 2)
        e = self.entry(self.gta, "facebook_reels", ids["facebook_reels"])
        self.assertEqual([p["label"] for p in e["points"]], ["1h", "72h"])
        self.assertEqual(e["points"][1]["age_min"], 78 * 60 + 10)
        self.assertEqual(e["missed"], ["3h", "6h", "24h"])
        st = json.loads((C.STATE / "stats" / "series" / "status.json").read_text())["accounts"]["gaming"]
        self.assertEqual(st["missed"][t.astimezone(self.cfg.tz()).date().isoformat()], 3)

    def test_3_not_public_yet_waits_and_tiktok_and_tests_are_never_read(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("youtube_shorts",))
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["outlets"]["tiktok"] = {"post_id": "v_pub", "publish_id": "v_pub", "platform_id": "",
                                                         "privacy": "SELF_ONLY", "published_at": "2031-03-04T14:00:00+00:00"}
        self.on(self.gta)
        self.yt.on("GET", r"/videos$", yt_answer(private=(ids["youtube_shorts"],)))
        went = self.went()
        self.run_at(went - timedelta(hours=1))               # before its time: the clock has not started
        self.assertEqual(self.yt.calls, [])
        self.run_at(went + timedelta(minutes=80))
        self.assertEqual(len(self.yt.calls), 1)
        self.assertEqual(self.yt.calls[0]["params"]["part"], "statistics,status")
        e = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])      # private: no point yet, one try
        self.assertEqual((e["points"], e["missed"], e["tries"]), ([], [], {"label": "1h", "n": 1}))
        self.yt.routes.clear()
        self.yt.on("GET", r"/videos$", yt_answer(views=900))
        self.run_at(went + timedelta(minutes=95))
        e = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])
        self.assertEqual([(p["label"], p["views"], p["src"]) for p in e["points"]], [("1h", 900, "data")])
        self.assertNotIn("shares", e["points"][0])          # YouTube's Data API has no shares: absent, never 0
        self.assertFalse([c for c in self.g.calls + self.yt.calls if "tiktok" in c["url"]])


# ------------------------------------------------------------------------------------------------------ the reads
class ReadsTest(Base):
    def test_1_youtube_asks_fifty_ids_a_call(self):
        day = DAY - timedelta(days=1)
        ids = [self.put(self.gta, day, f"05:{m:02d}", outlets=("youtube_shorts",))["youtube_shorts"]
               for m in range(60)]
        self.on(self.gta)
        self.yt.on("GET", r"/videos$", yt_answer())
        self.run_at(self.cfg.slot_time(DAY, "12:00"))
        sizes = [len(c["params"]["id"].split(",")) for c in self.yt.calls]
        self.assertEqual(sizes, [50, 10])
        self.assertEqual(sorted(i for c in self.yt.calls for i in c["params"]["id"].split(",")), sorted(ids))
        units = json.loads((C.STATE / "social" / "youtube-units.json").read_text())
        self.assertEqual((units["units"], units["by"]), (2, {"videos.list": 2}))
        e = self.entry(self.gta, "youtube_shorts", ids[0])
        self.assertEqual(e["points"][0]["label"], "24h")
        self.assertEqual(e["missed"], ["1h", "3h", "6h"])

    def test_2_keyed_by_the_platforms_id_in_the_month_it_went_public(self):
        ids = self.put(self.gta, DAY, "07:00")
        self.on(self.gta)
        self.meta_routes()
        self.yt.on("GET", r"/videos$", yt_answer())
        self.run_at(self.went() + timedelta(minutes=80))
        folder = C.STATE / "stats" / "series"
        names = sorted(p.name for p in folder.glob("*__*.json"))
        self.assertEqual(names, [f"gaming__{o}__2031-03.json" for o in sorted(ALL)])
        for o in ALL:
            d = json.loads((folder / f"gaming__{o}__2031-03.json").read_text())
            self.assertEqual(list(d), [ids[o]])
            e = d[ids[o]]
            self.assertEqual((e["board"], e["slot"], e["clip"], e["fmt"], e["dur"]), (DAY.isoformat(), "07:00",
                                                                                       "clip-0700", "room", 40.0))
            self.assertEqual(e["went"], self.went().astimezone(timezone.utc).isoformat())
            for k in ("missed", "early", "refreshed_at", "backfill", "gone_at"):
                self.assertIn(k, e)
        self.assertFalse((C.STATE / "stats" / "pulled").exists())
        self.assertFalse((C.STATE / "stats" / "history").exists())

    def test_3_extras_in_calls_of_their_own_and_the_views_kept_when_they_fail(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("instagram_reels",))
        self.on(self.gta, "checkpoints", "checkpoints.extra")

        def refuse(ms):
            raise meta_error(1, msg="(#1) An unknown error occurred", transient=True)
        self.meta_routes(ig=ig_answer(views=321, extra=refuse))
        self.run_at(self.went() + timedelta(hours=28))
        metrics = [c["params"]["metric"] for c in self.g.calls]
        self.assertEqual(metrics[0], "views,likes,comments,shares")
        self.assertEqual(metrics[1], ",".join(API.IG_EXTRA))
        self.assertEqual(sorted(metrics[2:]), sorted(API.IG_EXTRA))      # each asked alone, once
        e = self.entry(self.gta, "instagram_reels", ids["instagram_reels"])
        self.assertEqual((e["points"][0]["label"], e["points"][0]["views"], e["points"][0]["extra"]), ("24h", 321, {}))
        self.assertFalse((C.STATE / "stats" / "series" / "unsupported.json").exists())   # not a metric's fault

    def test_4_a_refused_metric_is_remembered_and_not_asked_again(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("instagram_reels",))
        ids2 = self.put(self.gta, DAY, "12:00", outlets=("instagram_reels",), tag="b")
        self.on(self.gta, "checkpoints", "checkpoints.extra")

        def extra(ms):
            if "reels_skip_rate" in ms:
                raise meta_error(100, msg="(#100) The reels_skip_rate metric is not supported for this media")
            return {"data": [{"name": m, "values": [{"value": 12000 if "time" in m else 9}]} for m in ms]}
        self.meta_routes(ig=ig_answer(extra=extra))
        self.run_at(self.went() + timedelta(hours=27, minutes=50))
        un = json.loads((C.STATE / "stats" / "series" / "unsupported.json").read_text())
        self.assertEqual(list(un["instagram"]), ["reels_skip_rate"])
        e = self.entry(self.gta, "instagram_reels", ids["instagram_reels"])
        x = e["points"][0]["extra"]
        self.assertEqual(sorted(x), sorted(m for m in API.IG_EXTRA if m != "reels_skip_rate"))
        self.assertNotIn("reels_skip_rate", x)                           # absent, never 0
        before = len(self.g.calls)
        self.run_at(self.went(DAY, "12:00") + timedelta(hours=27, minutes=50))
        new = [c["params"]["metric"] for c in self.g.calls[before:]]
        self.assertEqual(new, ["views,likes,comments,shares",
                               ",".join(m for m in API.IG_EXTRA if m != "reels_skip_rate")])
        pts = self.entry(self.gta, "instagram_reels", ids2["instagram_reels"])["points"]
        self.assertEqual([p["label"] for p in pts], ["6h", "24h"])      # 6h read in the first run, no extras then
        self.assertEqual((pts[0]["extra"], sorted(pts[1]["extra"])),
                         ({}, sorted(m for m in API.IG_EXTRA if m != "reels_skip_rate")))
        self.assertEqual(CP.unsupported("instagram", self.clock() + timedelta(days=31)), [])   # asked again later

    def test_5_facebook_extras_and_its_retention_at_7d(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        self.on(self.gta, "checkpoints", "checkpoints.extra")

        def extra(ms):
            if ms == [API.FB_RETENTION]:
                return {"data": [{"name": API.FB_RETENTION, "values": [{"value": {"0": 100, "1": 80, "2": 50}}]}]}
            return {"data": [{"name": m, "values": [{"value": 4200 if m == "post_video_avg_time_watched" else 3}]}
                             for m in ms]}
        self.meta_routes(fb=fb_answer(extra=extra))
        self.run_at(self.went() + timedelta(days=7, hours=7))
        metrics = [c["params"]["metric"] for c in self.g.calls]
        self.assertEqual(metrics, [",".join(API.FB_BASE), ",".join(API.FB_EXTRA), API.FB_RETENTION])
        x = self.entry(self.gta, "facebook_reels", ids["facebook_reels"])["points"][0]["extra"]
        self.assertEqual(x["retention"], [[0.0, 1.0], [0.5, 0.8], [1.0, 0.5]])
        self.assertEqual(x["post_video_avg_time_watched"], 4200)
        self.assertEqual(CP._norm_extra("facebook_reels", x)["avg_watch_s"], 4.2)

    def test_6_youtube_analytics_only_with_its_switch_and_permission(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("youtube_shorts",))
        self.on(self.gta, "checkpoints", "checkpoints.yt_analytics")
        self.yt.on("GET", r"/videos$", yt_answer())
        self.yt.on("GET", r"/v2/reports$", lambda call: (
            {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}],
             "rows": [[0.01, 1.1], [0.5, 0.7], [1.0, 0.3]]} if call["params"].get("dimensions") else
            {"columnHeaders": [{"name": m} for m in API.YTA_METRICS], "rows": [[120, 21.5, 64.2, 3, 2]]}))
        self.run_at(self.went() + timedelta(hours=78, minutes=10))          # 72h, no permission read yet
        self.assertEqual([c["path"] for c in self.yt.calls], ["/videos"])
        CAP.save_scopes(self.gta, "youtube", ["youtube.readonly", "youtube.upload", "yt-analytics.readonly"],
                        "connect")
        self.run_at(self.went() + timedelta(days=7, hours=7))
        self.assertEqual([c["path"] for c in self.yt.calls], ["/videos", "/videos", "/v2/reports", "/v2/reports"])
        q = self.yt.calls[2]["params"]
        self.assertEqual((q["ids"], q["filters"], q["metrics"]), ("channel==MINE", f"video=={ids['youtube_shorts']}",
                                                                  ",".join(API.YTA_METRICS)))
        pt = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])["points"][-1]
        self.assertEqual((pt["label"], pt["extra"]["engagedViews"], pt["extra"]["averageViewDuration"]),
                         ("7d", 120, 21.5))
        self.assertEqual(pt["extra"]["retention"][0], [0.01, 1.1])

    def test_7_the_boards_numbers_move_only_when_newer_and_the_nightly_files_stay(self):
        old = {"views": 5, "likes": 1, "comments": 0, "shares": 0, "at": "2031-03-04T13:00:00+00:00"}
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",), metrics=old)
        newer = {"views": 999, "likes": 9, "comments": 9, "shares": 9, "at": "2031-03-05T00:00:00+00:00"}
        ids2 = self.put(self.gta, DAY, "08:00", outlets=("facebook_reels",), tag="z", metrics=newer)
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=77))
        self.run_at(self.went(DAY, "08:00") + timedelta(minutes=80))
        slots = B.read(self.gta, DAY)["slots"]
        m = slots["07:00"]["outlets"]["facebook_reels"]["metrics"]
        self.assertEqual((m["views"], m["likes"], m["comments"], m["shares"]), (77, 4, 2, 1))
        self.assertEqual(slots["08:00"]["outlets"]["facebook_reels"]["metrics"], newer)      # newer than this read
        self.assertTrue(self.entry(self.gta, "facebook_reels", ids["facebook_reels"]))
        self.assertTrue(self.entry(self.gta, "facebook_reels", ids2["facebook_reels"]))
        self.assertFalse((C.STATE / "stats" / "pulled").exists())

    def test_8_the_nightly_reads_lock_is_never_opened(self):
        self.put(self.gta, DAY, "07:00")
        self.on(self.gta, "checkpoints", "checkpoints.extra")
        self.meta_routes()
        self.yt.on("GET", r"/videos$", yt_answer())
        opened = []
        real_open, real_os_open = builtins.open, os.open

        def spy(file, *a, **kw):
            opened.append(str(file))
            return real_open(file, *a, **kw)

        def os_spy(path, *a, **kw):
            opened.append(str(path))
            return real_os_open(path, *a, **kw)
        with mock.patch.object(builtins, "open", spy), mock.patch.object(os, "open", os_spy):
            self.run_at(self.went() + timedelta(hours=28))
            CP.backfill(self.cfg, now=self.clock())
            CP.get_series(self.cfg, {"view": "extras"})
            CP.card_part(self.cfg, self.gta, DAY, B.read(self.gta, DAY)["slots"]["07:00"], self.clock())
        self.assertTrue(any(p.endswith("series/.lock") for p in opened))
        self.assertFalse([p for p in opened if p.endswith("read.lock")])
        self.assertFalse((C.STATE / "stats" / "read.lock").exists())

    def test_9_a_meta_stop_ends_that_platform_for_the_tick_and_the_guard_hears_it(self):
        for k in ("07:00", "08:00", "09:00"):
            self.put(self.gta, DAY, k, outlets=("facebook_reels", "instagram_reels"))
        self.on(self.gta, "checkpoints")
        SW.set_mode(self.cfg, self.gta, "guard", "on", "test")
        self.g.on("GET", r"/video_insights$", meta_error(4, msg="(#4) Application request limit reached"))
        self.g.on("GET", r"/insights$", ig_answer())
        lines = self.run_at(self.went(DAY, "09:00") + timedelta(minutes=80))
        fb = [c for c in self.g.calls if c["path"].endswith("/video_insights")]
        ig = [c for c in self.g.calls if c["path"].endswith("/insights")]
        self.assertEqual((len(fb), len(ig)), (1, 3))
        self.assertTrue(any("Facebook said stop" in ln for ln in lines), lines)
        self.assertIn("rate limit", OW.held(self.cfg, self.gta, "facebook", "read", self.clock()))

    def test_10_a_gone_reel_is_kept_and_a_gone_youtube_video_loses_its_numbers(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels", "youtube_shorts"))
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=40))
        self.yt.on("GET", r"/videos$", yt_answer(views=60))
        self.run_at(self.went() + timedelta(minutes=80))
        self.g.routes.clear()
        self.yt.routes.clear()
        self.g.on("GET", r"/video_insights$", meta_error(100, http=404, msg="(#100) Object does not exist"))
        self.yt.on("GET", r"/videos$", yt_answer(gone=(ids["youtube_shorts"],)))
        self.run_at(self.went() + timedelta(hours=3, minutes=40))
        fb = self.entry(self.gta, "facebook_reels", ids["facebook_reels"])
        yt = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])
        self.assertTrue(fb["gone_at"] and yt["gone_at"])
        self.assertEqual([p["label"] for p in fb["points"]], ["1h"])
        self.assertEqual(yt["points"], [])
        n = len(self.g.calls) + len(self.yt.calls)
        self.run_at(self.went() + timedelta(hours=7))
        self.assertEqual(len(self.g.calls) + len(self.yt.calls), n)          # never asked about again

    def test_11_stored_youtube_numbers_are_refreshed_within_30_days(self):
        self.on(self.gta)
        went = self.went(DAY - timedelta(days=60))
        folder = C.STATE / "stats" / "series"
        folder.mkdir(parents=True)
        old = {"went": went.isoformat(), "points": [{"label": "30d", "at": (went + timedelta(days=30)).isoformat(),
                                                     "views": 10}], "missed": [], "refreshed_at": None,
               "gone_at": None}
        (folder / "gaming__youtube_shorts__2031-01.json").write_text(json.dumps({"keep1": old, "lost1": old}))
        self.yt.on("GET", r"/videos$", yt_answer(views=44, gone=("lost1",)))
        self.run_at(self.clock())
        self.assertEqual(self.yt.calls[0]["params"]["id"], "keep1,lost1")
        d = json.loads((folder / "gaming__youtube_shorts__2031-01.json").read_text())
        self.assertEqual((d["keep1"]["latest"]["views"], d["keep1"]["refreshed_at"]), (44, CP._iso(self.clock())))
        self.assertEqual((d["lost1"]["points"], bool(d["lost1"]["gone_at"])), ([], True))
        self.run_at(self.clock() + timedelta(days=1))
        self.assertEqual(len(self.yt.calls), 1)                            # refreshed: not again for 29 days

    def test_12_slow_halves_the_meta_batch(self):
        for m in range(6):
            self.put(self.gta, DAY, f"07:{m:02d}", outlets=("facebook_reels",))
        self.on(self.gta)
        self.meta_routes()
        t = self.went() + timedelta(minutes=80)
        with mock.patch.object(CP, "META_BATCH", 4):
            OW.note_usage(self.cfg, self.gta, "facebook", {"buc_pct": 80.0}, t)
            lines = self.run_at(t)
            self.assertEqual(len(self.g.calls), 2)
            self.assertTrue(any("4 more next time (at most 2 a tick)" in ln for ln in lines), lines)
            OW.note_usage(self.cfg, self.gta, "facebook", {"buc_pct": 10.0}, t + timedelta(hours=2))
            self.run_at(t + timedelta(hours=2))
        self.assertEqual(len(self.g.calls), 6)


# ------------------------------------------------------------------------------------------------------ the guard
class HoldsTest(Base):
    def test_1_holds_skip_the_reads(self):
        ids = self.put(self.gta, DAY, "07:00")
        self.on(self.gta)
        self.meta_routes()
        self.yt.on("GET", r"/videos$", yt_answer())
        OW.hold(self.cfg, self.gta, "instagram", "read", 3600 * 6, "a test hold", now=self.clock())
        lines = self.run_at(self.went() + timedelta(minutes=80))
        self.assertEqual([c["path"] for c in self.g.calls], [f"/{ids['facebook_reels']}/video_insights"])
        self.assertEqual(len(self.yt.calls), 1)
        self.assertTrue(any("Instagram" in ln and "not read now" in ln for ln in lines), lines)
        O.meta_cool(self.gta, "Error validating access token")       # the poster's own Meta hold
        OW.hold(self.cfg, self.gta, "youtube", "*", 3600 * 6, "a test hold", now=self.clock())
        self.run_at(self.went() + timedelta(hours=3, minutes=40))
        self.assertEqual((len(self.g.calls), len(self.yt.calls)), (1, 1))

    def test_2_no_youtube_read_without_units_left(self):
        self.put(self.gta, DAY, "07:00", outlets=("youtube_shorts",))
        self.on(self.gta)
        self.yt.on("GET", r"/videos$", yt_answer())
        OW.yt_spend(self.cfg, 2450, "commentThreads.insert", True, self.clock())
        lines = self.run_at(self.went() + timedelta(minutes=80))
        self.assertEqual(self.yt.calls, [])
        self.assertTrue(any("units" in ln for ln in lines), lines)


# ------------------------------------------------------------------------------------------------------ early
class EarlyTest(Base):
    def seed(self, n: int, views: int = 100, label: str = "3h") -> None:
        folder = C.STATE / "stats" / "series"
        folder.mkdir(parents=True, exist_ok=True)
        data = {}
        for i in range(n):
            went = self.went(DAY - timedelta(days=2 + i))
            data[f"old{i}"] = {"went": went.astimezone(timezone.utc).isoformat(), "board": "x", "slot": "07:00",
                               "points": [{"label": label, "views": views + i, "at": went.isoformat()}],
                               "missed": [], "gone_at": None}
        (folder / "gaming__facebook_reels__2031-02.json").write_text(json.dumps(data))

    def test_1_running_hot_needs_eight_posts_and_twice_the_usual(self):
        self.seed(8)                                      # usual at 3h: median 103.5
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=259))
        self.run_at(self.went() + timedelta(hours=3, minutes=40))
        e = self.entry(self.gta, "facebook_reels", ids["facebook_reels"])
        self.assertEqual(e["early"], {"label": "3h", "x": 2.5, "vs_n": 8})
        self.assertEqual(CP.baseline("gaming", "facebook_reels", "3h", self.clock()), 104.0)   # with this one
        self.assertEqual(CP.baseline_n("gaming", "facebook_reels", "3h", self.clock(), exclude=ids[
            "facebook_reels"]), (103.5, 8))
        chips = CP.summary(self.cfg, self.clock(), [self.gta, self.led])
        self.assertEqual(chips, [{"key": f"checkpoints-hot-{ids['facebook_reels']}",
                                  "words": "Running hot: Clip at 07:00 2.5x at 3h",
                                  "href": f"/?date={DAY}&acct=gaming", "level": "calm", "order": 30}])
        s = B.read(self.gta, DAY)["slots"]["07:00"]
        part = CP.card_part(self.cfg, self.gta, DAY, s, self.clock())
        ink = 'style="color:var(--ink);font-weight:650"'
        self.assertEqual(part, '<div class="cp" aria-label="How it is doing"><div class="note cp-line hot">3h: '
                               f'<b {ink}>259</b>, <b style="color:var(--ok);font-weight:650">2.5x</b> your usual on '
                               'Facebook</div></div>')
        self.assertEqual(CP.CARD_CSS, "")
        self.assertEqual(SOC.brief(self.cfg, self.clock(), "all")[0]["words"], "Running hot: Clip at 07:00 2.5x at 3h")
        self.assertEqual(CP.summary(self.cfg, self.clock() + timedelta(hours=25), [self.gta]), [])    # a day on

    def test_2_slow_start_and_too_few_posts(self):
        self.seed(7)
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=10))
        self.run_at(self.went() + timedelta(hours=3, minutes=40))
        e = self.entry(self.gta, "facebook_reels", ids["facebook_reels"])
        self.assertIsNone(e["early"])                       # 7 posts: no usual yet
        self.assertEqual(CP.card_part(self.cfg, self.gta, DAY, B.read(self.gta, DAY)["slots"]["07:00"],
                                      self.clock()),
                         '<div class="cp" aria-label="How it is doing"><div class="note cp-line">3h: <b style="'
                         'color:var(--ink);font-weight:650">10</b> on Facebook</div></div>')
        self.seed(9)
        ids2 = self.put(self.gta, DAY, "08:00", outlets=("facebook_reels",), tag="s")
        self.run_at(self.went(DAY, "08:00") + timedelta(hours=3, minutes=40))
        e = self.entry(self.gta, "facebook_reels", ids2["facebook_reels"])
        self.assertEqual(e["early"]["x"], 0.1)
        chips = CP.summary(self.cfg, self.clock(), [self.gta])
        self.assertTrue(chips[-1]["words"].startswith("Slow start: Clip at 08:00"))
        self.assertEqual(CP.early(self.cfg, self.gta, self.clock())[-1]["kind"], "slow")

    def test_3_one_chip_per_clip(self):
        self.seed(8)
        folder = C.STATE / "stats" / "series"
        ig = json.loads((folder / "gaming__facebook_reels__2031-02.json").read_text())
        (folder / "gaming__instagram_reels__2031-02.json").write_text(json.dumps(ig))
        self.put(self.gta, DAY, "07:00", outlets=("facebook_reels", "instagram_reels"))
        self.on(self.gta)
        self.meta_routes(ig=ig_answer(views=400), fb=fb_answer(views=300))
        self.run_at(self.went() + timedelta(hours=3, minutes=40))
        self.assertEqual(len(CP.early(self.cfg, self.gta, self.clock())), 2)
        chips = CP.summary(self.cfg, self.clock(), [self.gta])
        self.assertEqual([c["words"] for c in chips], ["Running hot: Clip at 07:00 3.9x at 3h"])

    def test_4_numbers_read_like_the_stats_tab(self):
        self.assertEqual([CP.fmt_n(n) for n in (7, 999, 1234, 12345, 1234567)], ["7", "999", "1.2K", "12K", "1.2M"])
        self.assertEqual([CP.fmt_x(x) for x in (2.4, 2.0, 0.35, 12.3)], ["2.4x", "2x", "0.3x", "12x"])


# ------------------------------------------------------------------------------------------------------ backfill
class BackfillTest(Base):
    def test_1_points_from_the_nightly_snapshots(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("youtube_shorts", "facebook_reels"))
        went = self.went()
        slot = B.read(self.gta, DAY)["slots"]["07:00"]
        sid = {o: ST.post_id({"account": "gaming", "platform": ST.DASH_PLATFORM[o], "pid": ids[o]})
               for o in ids}
        # the stats ids are the nightly read's own (posts_from_boards)
        self.assertEqual(sorted(sid.values()), sorted(p["id"] for p in ST.posts_from_boards(self.cfg, days=3650)
                                                      if p["board"] == DAY.isoformat()))
        hist = C.STATE / "stats" / "history"
        hist.mkdir(parents=True)
        reads = []
        for age_h, views in ((-1, 0), (1.1, 30), (2.0, 55), (25.0, 400), (40.0, 600)):
            at = went + timedelta(hours=age_h)
            reads.append({"at": at.astimezone(timezone.utc).isoformat(), "posts": {
                sid["youtube_shorts"]: [views, 3, 1, 0, int(went.timestamp())], "otherpost": [9, 9, 9, 9, 0]}})
        (hist / f"{DAY}.json").write_text(json.dumps({"day": DAY.isoformat(), "reads": reads}))
        with self.assertRaises(SystemExit):
            CP.backfill(self.cfg, now=went + timedelta(hours=50))           # off: nothing written
        self.assertFalse((C.STATE / "stats" / "series").exists())
        self.on(self.gta)
        lines = CP.backfill(self.cfg, now=went + timedelta(hours=50))
        self.assertEqual(lines[0], "Gaming: 1 post backfilled with 2 points from 5 snapshots")
        e = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])
        self.assertEqual([(p["label"], p["views"], p["src"], p["age_min"]) for p in e["points"]],
                         [("1h", 30, "history", 66), ("24h", 400, "history", 1500)])
        self.assertTrue(e["backfill"])
        self.assertEqual(e["missed"], ["3h", "6h"])          # 2.0h is too far from 3h; 24h passed, kept
        self.assertIsNone(self.entry(self.gta, "facebook_reels", ids["facebook_reels"]))   # no snapshot of it
        self.assertEqual(slot["outlets"]["youtube_shorts"]["video_id"], ids["youtube_shorts"])
        # a run then reads nothing it already has; at 72h it reads 72h only
        self.yt.on("GET", r"/videos$", yt_answer(views=900))
        self.meta_routes()
        self.run_at(went + timedelta(hours=50))
        self.assertEqual(self.yt.calls, [])
        self.run_at(went + timedelta(hours=78, minutes=10))
        e = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])
        self.assertEqual([p["label"] for p in e["points"]], ["1h", "24h", "72h"])
        again = CP.backfill(self.cfg, now=went + timedelta(hours=80))
        self.assertEqual(again[0], "Gaming: 0 posts backfilled with 0 points from 5 snapshots")

    def test_2_history_points_math(self):
        went = datetime(2031, 1, 1, tzinfo=timezone.utc)
        reads = [(went + timedelta(minutes=m), [m, 0, 0, 0, 0]) for m in (50, 75, 170, 1300, 1500, 9000)]
        pts = CP.history_points(went, reads)
        self.assertEqual({k: v["views"] for k, v in pts.items()}, {"1h": 50, "3h": 170, "24h": 1500, "7d": 9000})


# ------------------------------------------------------------------------------------------------------ pages, off
class PagesTest(Base):
    def fill(self) -> dict:
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels", "instagram_reels"))
        self.on(self.gta, "checkpoints", "checkpoints.extra")

        def extra(ms):
            return {"data": [{"name": m, "values": [{"value": 15500 if m == "ig_reels_avg_watch_time" else 0.4
                                                     if m == "reels_skip_rate" else 77}]} for m in ms]}
        self.meta_routes(ig=ig_answer(views=120, extra=extra))
        self.run_at(self.went() + timedelta(hours=28))
        return ids

    def test_1_the_series_route(self):
        ids = self.fill()
        get, _ = serve(self, self.cfg)
        code, h, body = get(f"/stats/series?acct=gaming&outlet=instagram_reels&pid={ids['instagram_reels']}")
        self.assertEqual(code, 200)
        self.assertTrue(h["Content-Type"].startswith("application/json"))
        d = json.loads(body)
        self.assertEqual((d["on"], d["points"][0]["label"], d["points"][0]["views"]), (True, "24h", 120))
        self.assertEqual(d["labels"][0], ["1h", 60])
        self.assertIn("usual", d)
        code, _, body = get("/stats/series?view=extras")
        x = json.loads(body)
        self.assertEqual(x["on"], ["gaming"])
        self.assertEqual(x["posts"][f"instagram|{ids['instagram_reels']}"],
                         {"avg_watch_s": 15.5, "skip_rate": 40.0, "reach": 77.0, "saves": 77.0})
        self.assertEqual(get("/stats/series?view=extras", {"Host": "evil.example"})[0], 403)
        self.assertEqual(get("/stats/series?acct=gaming&outlet=tiktok&pid=1")[0], 400)
        self.assertEqual(get("/stats/series?acct=nobody&outlet=facebook_reels&pid=1")[0], 400)
        self.assertEqual(get("/stats/series?acct=gaming&outlet=facebook_reels&pid=<x>")[0], 400)
        d = json.loads(get("/stats/series?acct=politics&outlet=facebook_reels&pid=123")[2])
        self.assertEqual((d["on"], "points" in d), (False, False))     # off: nothing from its files
        n = len(self.g.calls)
        for _ in range(3):
            get(f"/stats/series?acct=gaming&outlet=instagram_reels&pid={ids['instagram_reels']}")
        self.assertEqual(len(self.g.calls), n)                          # a GET never reaches a platform

    def test_2_stats_data_is_free_of_series(self):
        self.fill()
        now = self.clock()
        SP._cache["key"] = None
        with_series = json.dumps(SP.data(self.cfg, now), sort_keys=True)
        self.assertNotIn('"points"', with_series)
        self.assertNotIn("series", with_series)
        shutil.move(str(C.STATE / "stats" / "series"), str(self.tmp / "series-aside"))
        SP._cache["key"] = None
        self.assertEqual(json.dumps(SP.data(self.cfg, now), sort_keys=True), with_series)

    def test_3_the_stats_tab_knows_the_part_is_built(self):
        get, _ = serve(self, self.cfg)
        code, _, body = get("/stats")
        page = body.decode()
        self.assertEqual(code, 200)
        self.assertIn("const SERIES = true;", page)
        self.assertIn('id="f-rank"', page)
        with mock.patch.object(SP, "_series_built", lambda: False):
            self.assertIn("const SERIES = false;", SP.page("t", "", "", "", "all"))

    def test_4_off_means_off(self):
        self.put(self.gta, DAY, "07:00")
        self.assertEqual(CP.run(self.cfg, now=self.went() + timedelta(hours=28)), [])
        self.assertEqual((self.g.calls, self.yt.calls), ([], []))
        self.assertFalse((C.STATE / "stats" / "series").exists())
        s = B.read(self.gta, DAY)["slots"]["07:00"]
        self.assertEqual(CP.card_part(self.cfg, self.gta, DAY, s, self.clock()), "")
        self.assertEqual(CP.summary(self.cfg, self.clock(), list(self.cfg.accounts.values())), [])
        self.assertEqual(SOC.run_job(self.cfg, "checkpoints", quiet=True, now=self.clock()), [])
        self.assertEqual((self.g.calls, self.yt.calls), ([], []))
        for good, words, _ in CP.doctor(self.cfg, self.clock()):
            self.assertIsNone(good, words)
            self.assertIn("checkpoints: off (factory.py social on ", words)
        d = json.loads(CP.get_series(self.cfg, {"view": "extras"})[2])
        self.assertEqual(d, {"on": [], "posts": {}, "tracked": []})


# ------------------------------------------------------------------------------------------------------ doctor, cli
class DoctorCliTest(Base):
    def test_1_the_doctor_says_what_each_part_waits_for(self):
        self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        self.on(self.gta, "checkpoints", "checkpoints.yt_analytics")
        rows = CP.doctor(self.cfg, self.clock())
        words = [w for _, w, _ in rows]
        self.assertIn("Gaming checkpoints: 0 posts tracked, last read never, 0 missed today; the timer has not "
                      "run it yet (every 15 minutes)", words)
        self.assertIn("Gaming YouTube Analytics: needs yt-analytics.readonly (factory.py connect youtube "
                      "gaming --client F --with analytics) and the API enabled in the politics project", words)
        self.assertIn("Gaming extra numbers: off (factory.py social on gaming checkpoints.extra)", words)
        self.assertIn("Politics checkpoints: off (factory.py social on politics checkpoints); "
                      "checkpoints.extra and checkpoints.yt_analytics wait for it, YouTube Analytics also for "
                      "yt-analytics.readonly (factory.py connect youtube politics --client F --with analytics) "
                      "and the API enabled", words)
        self.meta_routes()
        t = self.went() + timedelta(minutes=80)
        self.run_at(t)
        rows = CP.doctor(self.cfg, t + timedelta(minutes=6))
        self.assertIn((True, "Gaming checkpoints: 1 post tracked, last read 6 min ago, 0 missed today", ""),
                      rows)
        rows = CP.doctor(self.cfg, t + timedelta(hours=3))
        self.assertTrue([r for r in rows if r[0] is False and "the last run was 3 hours ago" in r[1]], rows)
        with mock.patch.object(CP, "_status", side_effect=RuntimeError("boom")):
            rows = CP.doctor(self.cfg, t)
        self.assertTrue([r for r in rows if r[0] is False and "could not read" in r[1]])   # never raises

    def test_2_the_timer_job_and_the_command(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        self.on(self.gta)
        self.meta_routes()
        t = self.went() + timedelta(minutes=80)
        self.clock.set(t)
        said = SOC.run_job(self.cfg, "checkpoints", quiet=True, now=t)
        self.assertEqual(said, ["Gaming: 1 checkpoint read (1 Facebook)"])
        tick = json.loads((C.STATE / "social" / "tick.json").read_text())
        self.assertEqual(tick["checkpoints"]["steps"], [["checkpoints:run", "ok"]])
        self.assertEqual(SOC.run_job(self.cfg, "checkpoints", quiet=True, now=t), [])      # nothing new: quiet
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            REG.run_cli(self.cfg, "checkpoints", ["show", "gaming", ids["facebook_reels"]])
            REG.run_cli(self.cfg, "checkpoints", ["status", "gaming"])
        text = out.getvalue()
        self.assertIn(f"Gaming Facebook {ids['facebook_reels']}: Clip at 07:00", text)
        self.assertIn(" 1h at 80 min (insights): views 200, likes 4, comments 2, shares 1", text)
        self.assertIn("Gaming (gaming): checkpoints on, extra numbers off, YouTube Analytics off", text)
        self.assertIn("today: 1 read, 0 missed", text)
        with self.assertRaises(SystemExit):
            REG.run_cli(self.cfg, "checkpoints", ["show", "gaming", "nope"])
        self.assertNotIn("\u2014", text + CP.__doc__)
        self.assertNotIn("!", text)


# ------------------------------------------------------------------------------------------------------ review fixes
class ReviewFixesTest(Base):
    """The review of 2026-10-20: each finding reproduced, then held."""

    def test_1_a_post_whose_went_time_moves_month_stays_in_one_file_and_is_not_read_again(self):
        day = date(2031, 2, 28)
        ids = self.put(self.gta, day, "16:59", outlets=("facebook_reels",))
        pid = ids["facebook_reels"]
        with B.edit(self.cfg, self.gta, day) as bd:              # the poster's clock: just after 00:00Z on the 1st
            bd["slots"]["16:59"]["outlets"]["facebook_reels"]["published_at"] = "2031-03-01T00:00:05+00:00"
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=40))
        t0 = datetime(2031, 3, 1, 1, 20, tzinfo=timezone.utc)
        self.run_at(t0)
        with B.edit(self.cfg, self.gta, day) as bd:              # the nightly read: Facebook's own time, February
            bd["slots"]["16:59"]["outlets"]["facebook_reels"]["published_at"] = "2031-02-28T23:59:40+00:00"
        n0 = len(self.g.calls)
        for k in range(8):
            self.run_at(t0 + timedelta(hours=2, minutes=30 + 15 * k))
        self.assertEqual(len(self.g.calls) - n0, 1)                 # the 3h read once, never the 1h again
        folder = C.STATE / "stats" / "series"
        self.assertEqual(sorted(p.name for p in folder.glob("gaming__facebook_reels__*.json")),
                         ["gaming__facebook_reels__2031-03.json"])
        self.assertEqual([x["label"] for x in self.entry(self.gta, "facebook_reels", pid)["points"]], ["1h", "3h"])
        self.assertEqual(CP.tracked("gaming", t0 + timedelta(hours=4)), 1)

    def test_2_an_older_copy_in_another_month_is_ignored_and_dropped_at_the_next_write(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        pid = ids["facebook_reels"]
        went = self.went()
        folder = C.STATE / "stats" / "series"
        folder.mkdir(parents=True)
        full = {"went": went.astimezone(timezone.utc).isoformat(), "board": DAY.isoformat(), "slot": "07:00",
                "points": [{"label": "1h", "views": 10, "at": (went + timedelta(hours=1)).isoformat()},
                           {"label": "3h", "views": 30, "at": (went + timedelta(hours=3)).isoformat()}],
                "missed": [], "gone_at": None}
        stale = dict(full, points=full["points"][:1])
        (folder / "gaming__facebook_reels__2031-03.json").write_text(json.dumps({pid: full}))
        (folder / "gaming__facebook_reels__2031-04.json").write_text(json.dumps({pid: stale}))
        CP._CACHE.clear()
        self.assertEqual([x["label"] for x in CP.series("gaming", "facebook_reels", pid)["points"]], ["1h", "3h"])
        self.assertEqual(len(list(CP._entries("gaming", "facebook_reels"))), 1)
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=70))
        self.run_at(went + timedelta(hours=6, minutes=50))
        self.assertEqual(len(self.g.calls), 1)                      # 6h only: the 3h in the March file counted
        self.assertEqual(json.loads((folder / "gaming__facebook_reels__2031-04.json").read_text()), {})
        e = self.entry(self.gta, "facebook_reels", pid)
        self.assertEqual([x["label"] for x in e["points"]], ["1h", "3h", "6h"])

    def test_3_one_post_refused_is_a_try_on_that_post_and_the_others_are_still_read(self):
        old = self.put(self.gta, DAY, "05:00", outlets=("instagram_reels",), tag="o")
        young = self.put(self.gta, DAY, "07:00", outlets=("instagram_reels",))
        self.on(self.gta)
        ypid, opid = young["instagram_reels"], old["instagram_reels"]
        err = meta_error(10, msg="(#10) Not enough viewers on the media to show insights")

        def ans(call):
            if ypid in call["path"]:
                raise err
            return ig_answer(views=50)(call)
        self.g.on("GET", r"/\d+/insights$", ans)
        for h in range(1, 12):
            self.run_at(self.went() + timedelta(hours=h, minutes=20))
        o = self.entry(self.gta, "instagram_reels", opid)
        y = self.entry(self.gta, "instagram_reels", ypid)
        self.assertTrue(o["points"])                                 # the older reel is read
        self.assertEqual(y["points"], [])
        self.assertEqual(y["missed"], ["1h", "3h", "6h"])            # at most GIVE_UP tries a label, then missed
        calls_y = [c for c in self.g.calls if ypid in c["path"]]
        self.assertLessEqual(len(calls_y), 3 * CP.GIVE_UP)

    def test_4_two_posts_refused_in_one_tick_end_that_platform_for_the_tick(self):
        for k in ("07:00", "08:00", "09:00"):
            self.put(self.gta, DAY, k, outlets=("instagram_reels",))
        self.on(self.gta)
        self.g.on("GET", r"/\d+/insights$", meta_error(10, msg="(#10) Application does not have permission"))
        lines = self.run_at(self.went(DAY, "09:00") + timedelta(minutes=80))
        self.assertEqual(len(self.g.calls), CP.POST_STOP)
        self.assertTrue(any("posts refused this tick" in ln for ln in lines), lines)

    def test_5_a_late_catch_up_read_is_kept_but_never_compared_with_the_usual(self):
        EarlyTest.seed(self, 8, views=100, label="6h")
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        pid = ids["facebook_reels"]
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=300))
        self.run_at(self.went() + timedelta(hours=23))               # the Mac slept until 23h
        e = self.entry(self.gta, "facebook_reels", pid)
        self.assertEqual([(x["label"], x["age_min"], x["views"], x.get("late")) for x in e["points"]],
                         [("6h", 1380, 300, True)])
        self.assertIsNone(e["early"])
        self.assertEqual(CP.summary(self.cfg, self.clock(), [self.gta]), [])
        self.assertEqual(CP.baseline_n("gaming", "facebook_reels", "6h", self.clock()), (103.5, 8))
        part = CP.card_part(self.cfg, self.gta, DAY, B.read(self.gta, DAY)["slots"]["07:00"], self.clock())
        self.assertIn("6h (read at 23 h): ", part)
        self.assertNotIn("your usual", part)
        # an on-time read is not late: the 1h of a post read at 80 minutes
        self.assertFalse(CP._is_late({"label": "1h", "age_min": 84}))
        self.assertFalse(CP._is_late({"label": "3h", "age_min": 180 + 27 + 30}))
        self.assertTrue(CP._is_late({"label": "6h", "age_min": 1380}))

    def test_6_youtube_numbers_go_after_30_days_with_the_switch_off_and_no_call(self):
        folder = C.STATE / "stats" / "series"
        folder.mkdir(parents=True)
        old = self.went(DAY - timedelta(days=90))
        f = folder / "gaming__youtube_shorts__2030-12.json"
        f.write_text(json.dumps({"vidOLD": {
            "went": old.astimezone(timezone.utc).isoformat(), "board": "x", "slot": "07:00",
            "points": [{"label": "24h", "views": 500, "likes": 3, "at": (old + timedelta(days=1)).isoformat(),
                        "extra": {"engagedViews": 400}},
                       {"label": "30d", "views": 900, "at": (self.clock() - timedelta(days=10)).isoformat(),
                        "extra": {}}],
            "missed": ["1h"], "early": {"label": "24h", "x": 1}, "latest": {"views": 1000, "at": (
                old + timedelta(days=40)).isoformat()}, "refreshed_at": None, "gone_at": None}}))
        fb = folder / "gaming__facebook_reels__2030-12.json"
        fb.write_text(json.dumps({"r1": {"went": old.isoformat(), "points": [{"label": "24h", "views": 5, "at": (
            old + timedelta(days=1)).isoformat()}], "missed": [], "gone_at": None}}))
        CP._CACHE.clear()
        rows = CP.doctor(self.cfg, self.clock())
        self.assertTrue([r for r in rows if r[0] is False and "1 YouTube post still holds numbers read 30 days ago" in r[1]], rows)
        lines = CP.run(self.cfg, now=self.clock())                   # every account off
        self.assertEqual((self.g.calls, self.yt.calls), ([], []))
        self.assertTrue(any("YouTube's 30-day rule" in ln for ln in lines), lines)
        e = json.loads(f.read_text())["vidOLD"]
        self.assertEqual(e["points"][0], {"label": "24h", "at": (old + timedelta(days=1)).isoformat(), "extra": {},
                                          "expired": True})
        self.assertEqual(e["points"][1]["views"], 900)               # read 10 days ago: kept
        self.assertNotIn("latest", e)
        self.assertIsNone(e["early"])
        self.assertEqual(e["missed"], ["1h"])
        self.assertEqual(json.loads(fb.read_text())["r1"]["points"][0]["views"], 5)   # Meta's numbers stay
        CP._CACHE.clear()
        self.assertFalse([r for r in CP.doctor(self.cfg, self.clock()) if "YouTube post" in r[1]])
        self.assertEqual(CP.run(self.cfg, now=self.clock()), [])     # nothing left to delete: quiet again

    def test_7_a_private_youtube_video_is_tried_a_few_times_then_missed(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("youtube_shorts",))
        self.on(self.gta)
        self.yt.on("GET", r"/videos$", yt_answer(private=(ids["youtube_shorts"],)))
        start = self.went() + timedelta(days=2)
        for k in range(96):                                          # one day of ticks
            self.run_at(start + timedelta(minutes=15 * k))
        self.assertEqual(len(self.yt.calls), CP.GIVE_UP)
        e = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])
        self.assertEqual((e["points"], e["missed"]), ([], ["1h", "3h", "6h", "24h"]))

    def test_8_backfill_after_a_run_never_leaves_a_label_both_read_and_missed(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("youtube_shorts",))
        went = self.went()
        sid = ST.post_id({"account": "gaming", "platform": "youtube", "pid": ids["youtube_shorts"]})
        hist = C.STATE / "stats" / "history"
        hist.mkdir(parents=True)
        reads = [{"at": (went + timedelta(hours=h)).astimezone(timezone.utc).isoformat(),
                  "posts": {sid: [v, 1, 0, 0, int(went.timestamp())]}} for h, v in ((1.0, 30), (24.0, 400))]
        (hist / f"{DAY}.json").write_text(json.dumps({"day": DAY.isoformat(), "reads": reads}))
        self.on(self.gta)
        self.yt.on("GET", r"/videos$", yt_answer(views=900))
        self.run_at(went + timedelta(hours=50))
        e = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])
        self.assertEqual(([x["label"] for x in e["points"]], e["missed"]), (["24h"], ["1h", "3h", "6h"]))
        CP.backfill(self.cfg, now=went + timedelta(hours=50))
        e = self.entry(self.gta, "youtube_shorts", ids["youtube_shorts"])
        self.assertEqual(([x["label"] for x in e["points"]], e["missed"]), (["1h", "24h"], ["3h", "6h"]))

    def test_9_two_slots_holding_one_id_are_read_once_under_the_slot_that_posted_it(self):
        ids = self.put(self.gta, DAY, "08:00", outlets=("facebook_reels",))
        self.put(self.gta, DAY, "07:00", outlets=())
        with B.edit(self.cfg, self.gta, DAY) as bd:
            r = dict(bd["slots"]["08:00"]["outlets"]["facebook_reels"], provider="matched")
            bd["slots"]["07:00"].setdefault("outlets", {})["facebook_reels"] = r
        self.on(self.gta)
        self.meta_routes()
        self.run_at(self.went(DAY, "08:00") + timedelta(minutes=80))
        self.assertEqual(len(self.g.calls), 1)
        self.assertEqual(self.entry(self.gta, "facebook_reels", ids["facebook_reels"])["slot"], "08:00")

    def test_10_the_today_band_shows_the_hottest_across_every_account(self):
        folder = C.STATE / "stats" / "series"
        folder.mkdir(parents=True)
        now = self.clock()
        went = (now - timedelta(hours=5)).astimezone(timezone.utc).isoformat()

        def e(slot, x, title):
            return {"went": went, "board": str(DAY), "slot": slot, "title": title, "points": [],
                    "early": {"label": "3h", "x": x, "vs_n": 9}, "gone_at": None}
        (folder / "gaming__facebook_reels__2031-03.json").write_text(json.dumps(
            {"g1": e("07:00", 2.1, "GTA a"), "g2": e("08:00", 2.2, "GTA b")}))
        (folder / "politics__facebook_reels__2031-03.json").write_text(json.dumps(
            {"l1": e("07:00", 5.0, "Politics big")}))
        self.on(self.gta)
        self.on(self.led)
        CP._CACHE.clear()
        self.assertEqual([c["words"] for c in CP.summary(self.cfg, now, [self.gta, self.led])],
                         ["Running hot: Politics big 5x at 3h", "Running hot: GTA b 2.2x at 3h"])

    def test_11_how_it_grew_is_offered_only_where_there_is_a_series(self):
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        self.on(self.gta)
        self.meta_routes()
        self.run_at(self.went() + timedelta(minutes=80))
        d = json.loads(CP.get_series(self.cfg, {"view": "extras"})[2])
        self.assertEqual(d["tracked"], [f"facebook|{ids['facebook_reels']}"])
        one = json.loads(CP.get_series(self.cfg, {"acct": "gaming", "outlet": "facebook_reels",
                                                  "pid": "999999"})[2])
        self.assertIs(one["tracked"], False)
        self.assertIn("TRK.has(p.platform + '|' + p.pid)", SP.JS)
        for words in ("Not tracked: only the factory", "No checkpoint read for this post. Missed: ",
                      "YouTube lets these numbers be kept for 30 days"):
            self.assertIn(words, SP.JS)
        self.assertNotIn("The first comes about an hour after it went public", SP.JS)

    def test_12_the_chart_draws_one_read_and_its_figures_fit_at_280px(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        js = SP.JS
        src = "\n".join(_js_fn(js, n) for n in ("fmtN", "niceStep", "growLine", "growAxes", "growChart"))
        rank = "\n".join([_js_const(js, "sum"), _js_block(js, "const RANKS = ["), _js_const(js, "xOf"),
                          _js_fn(js, "rankVal")])
        script = r"""
const made = [];
function sv(tag, attrs) { const n = {tag, attrs: attrs || {}, kids: [], textContent: '', append(...k) { this.kids.push(...k); }}; made.push(n); return n; }
""" + src + "\n" + rank + r"""
let XT = {posts: {'instagram|a': {reach: 400, avg_watch_s: 10}, 'facebook|b': {reach: 2000, avg_watch_s: 20}}};
const out = {};
const L = ['1h', '3h', '6h', '24h', '72h', '7d', '30d'];
const box = {clientWidth: 184, replaceChildren(...k) { this.kids = k; }};
growChart(box, L, {'1h': 1200}, {'1h': 1000, '3h': 3000, '6h': 3500}, 'red');
const W = +made.find(n => n.tag === 'svg').attrs.width;
out.post = made.filter(n => n.tag === 'path' && n.attrs.stroke === 'red').map(n => n.attrs.d);
out.figs = made.filter(n => n.tag === 'text' && n.attrs['text-anchor'] === 'end' && +n.attrs.y < 160).map(n => [n.textContent, +n.attrs.x]);
const c = {posts: [{platform: 'instagram', pid: 'a'}, {platform: 'facebook', pid: 'b'}]};
out.reach = rankVal(c, 'reach'); out.watch = rankVal(c, 'avg_watch_s'); out.W = W;
console.log(JSON.stringify(out));
"""
        f = self.tmp / "grow.js"
        f.write_text(script)
        r = subprocess.run([node, str(f)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(len(out["post"]), 1)
        self.assertIn(" L", out["post"][0])                           # a visible mark, not a lone moveto
        self.assertTrue(out["figs"])
        for txt, x in out["figs"]:                                    # right-aligned at x: about 7px a figure
            self.assertGreaterEqual(x - 7 * len(txt), 0, (txt, x))
        self.assertEqual((out["reach"], out["watch"]), (2400, 15))  # reach added up, average watch averaged


class LiveCostTest(Base):
    """Review 2026-10-20: the Clips page redraws every card each 15 seconds, and each card asked about the whole
    history of each platform twice. Now one look per change of the files."""

    def history(self, months: int = 4, per: int = 40) -> None:
        """Posts on Facebook in month files before DAY: on-time, late, gone and future ones, and an older copy of one
        post in a second month (the index keeps the one with more read)."""
        folder = C.STATE / "stats" / "series"
        folder.mkdir(parents=True, exist_ok=True)
        for m in range(months):
            ym = (DAY.replace(day=1) - timedelta(days=1 + 31 * m)).strftime("%Y-%m")
            start = datetime.strptime(ym + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
            data = {}
            for i in range(per):
                went = start + timedelta(hours=17 * i)
                pt = {"label": "3h", "views": 100 + (i * 37 + m * 11) % 400, "at": went.isoformat(), "age_min": 185}
                if i % 9 == 4:
                    pt["late"] = True
                data[f"h{m}x{i}"] = {"went": went.isoformat(), "points": [pt], "missed": [],
                                     "gone_at": went.isoformat() if i % 13 == 6 else None}
            if m == 1:
                data["h0x3"] = {"went": data["h1x3"]["went"], "points": [], "missed": [], "gone_at": None}
            (folder / f"gaming__facebook_reels__{ym}.json").write_text(json.dumps(data))
        later = {"went": "2031-12-01T00:00:00+00:00", "points": [{"label": "3h", "views": 9, "at": "x"}],
                 "missed": [], "gone_at": None}
        (folder / "gaming__facebook_reels__2031-12.json").write_text(json.dumps({"future": later}))

    def naive(self, label, now, exclude=None):
        """The usual as the code first worked it out: every entry, filtered, sorted, the newest 30."""
        got = []
        for pid, e in CP._index("gaming", "facebook_reels").items():
            if pid == exclude or e[1].get("gone_at"):
                continue
            pt = CP._point(e[1], label)
            went = CP._when(e[1].get("went"))
            if not pt or not isinstance(pt.get("views"), (int, float)) or went is None or went > now or CP._is_late(pt):
                continue
            got.append((went, float(pt["views"])))
        got.sort(reverse=True)
        vals = [v for _, v in got[:CP.BASE_LAST]]
        return (None if len(vals) < CP.BASE_MIN else float(statistics.median(vals))), len(vals)

    def test_1_the_usual_is_the_same_as_before(self):
        self.history()
        for now in (self.clock(), datetime(2031, 2, 10, tzinfo=timezone.utc), datetime(2031, 1, 3, tzinfo=timezone.utc),
                    datetime(2032, 1, 1, tzinfo=timezone.utc)):
            for ex in (None, "h0x2", "h0x3", "future", "nope"):
                for lbl in ("3h", "6h"):
                    self.assertEqual(CP.baseline_n("gaming", "facebook_reels", lbl, now, exclude=ex),
                                     self.naive(lbl, now, ex), (now, ex, lbl))
        self.assertEqual(CP.baseline_n("gaming", "facebook_reels", "3h", datetime(2030, 1, 1, tzinfo=timezone.utc)),
                         (None, 0))

    def test_2_a_redraw_reads_no_history_until_a_file_changes(self):
        self.history()
        ids = self.put(self.gta, DAY, "07:00", outlets=("facebook_reels",))
        self.on(self.gta)
        self.meta_routes(fb=fb_answer(views=900))
        self.run_at(self.went() + timedelta(hours=3, minutes=40))
        s = B.read(self.gta, DAY)["slots"]["07:00"]
        first = CP.card_part(self.cfg, self.gta, DAY, s, self.clock())
        med, _ = CP.baseline_n("gaming", "facebook_reels", "3h", self.clock(), exclude=ids["facebook_reels"])
        self.assertIn(f"{CP.fmt_x(900 / med)}</b> your usual on Facebook", first)
        chips = CP.summary(self.cfg, self.clock(), [self.gta])
        with mock.patch.object(CP, "_point", wraps=CP._point) as pt, \
                mock.patch.object(CP, "_when", wraps=CP._when) as wh, \
                mock.patch.object(CP.store, "read", wraps=CP.store.read) as rd:
            for _ in range(5):                    # five redraws of the card and the Today band
                self.assertEqual(CP.card_part(self.cfg, self.gta, DAY, s, self.clock()), first)
                self.assertEqual(CP.summary(self.cfg, self.clock(), [self.gta]), chips)
        self.assertEqual((pt.call_count, wh.call_count, rd.call_count), (0, 0, 0))
        folder = C.STATE / "stats" / "series"     # a new read lands: the next redraw sees it
        p = folder / f"gaming__facebook_reels__{DAY:%Y-%m}.json"
        d = json.loads(p.read_text())
        for i in range(CP.BASE_LAST):
            w = self.went() - timedelta(hours=1 + i)
            d[f"new{i}"] = {"went": w.astimezone(timezone.utc).isoformat(), "points": [
                {"label": "3h", "views": 9000, "at": w.isoformat(), "age_min": 180}], "missed": [], "gone_at": None}
        p.write_text(json.dumps(d))
        self.assertEqual(CP.baseline_n("gaming", "facebook_reels", "3h", self.clock(),
                                       exclude=ids["facebook_reels"]), (9000.0, CP.BASE_LAST))
        self.assertIn("0.1x</b> your usual on Facebook", CP.card_part(self.cfg, self.gta, DAY, s, self.clock()))

    def test_3_the_folder_listing_follows_a_new_file(self):
        self.history(months=2)
        folder = C.STATE / "stats" / "series"
        old = folder.stat().st_mtime - 3600
        os.utime(folder, (old, old))              # an hour since the folder changed: its listing is kept
        names = [p.name for *_, p in CP._files("gaming", "facebook_reels")]
        with mock.patch.object(CP.os, "scandir", side_effect=AssertionError("listed again")):
            self.assertEqual([p.name for *_, p in CP._files("gaming", "facebook_reels")], names)
        (folder / "gaming__facebook_reels__2030-01.json").write_text("{}")
        self.assertEqual([p.name for *_, p in CP._files("gaming", "facebook_reels")],
                         names + ["gaming__facebook_reels__2030-01.json"])
        os.utime(folder, (old, old))
        (folder / "gaming__facebook_reels__2030-01.json").unlink()
        self.assertEqual([p.name for *_, p in CP._files("gaming", "facebook_reels")], names)

    def test_4_early_looks_only_at_the_last_day(self):
        self.history()
        with mock.patch.object(CP, "_by_went", wraps=CP._by_went) as bw:
            self.assertEqual(CP.early(self.cfg, self.gta, self.clock()), [])
        self.on(self.gta)
        folder = C.STATE / "stats" / "series"
        p = folder / f"gaming__facebook_reels__{DAY:%Y-%m}.json"
        rows = {}
        for i, x in enumerate((2.5, 0.3, 2.5, 3.0)):
            w = self.went() - timedelta(hours=2 + i)
            rows[f"e{i}"] = {"went": w.astimezone(timezone.utc).isoformat(), "points": [], "missed": [],
                             "gone_at": None, "title": f"T{i}", "early": {"label": "3h", "x": x, "vs_n": 8}}
        rows["e9"] = dict(rows["e0"], went=(self.went() - timedelta(hours=30)).isoformat())   # over a day ago
        p.write_text(json.dumps(rows))
        got = CP.early(self.cfg, self.gta, self.clock())
        self.assertEqual([(r["pid"], r["x"]) for r in got], [("e3", 3.0), ("e0", 2.5), ("e2", 2.5), ("e1", 0.3)])
        self.assertEqual(bw.call_count, 0)        # off: not looked at


def _js_fn(js: str, name: str) -> str:
    """A top-level function of the page's script, by brace matching."""
    i = js.index(f"function {name}(")
    j = js.index("{", i)
    depth = 0
    for k in range(j, len(js)):
        depth += {"{": 1, "}": -1}.get(js[k], 0)
        if depth == 0:
            return js[i:k + 1]
    raise AssertionError(name)


def _js_const(js: str, name: str) -> str:
    i = js.index(f"const {name} = ")
    return js[i:js.index("\n", i)]


def _js_block(js: str, start: str) -> str:
    i = js.index(start)
    return js[i:js.index("];", i) + 2]


class ParseTest(unittest.TestCase):
    def test_1_the_answers_parse_like_the_nightly_read(self):
        self.assertEqual(API.parse_fb({"data": [{"name": "blue_reels_play_count", "values": [{"value": 10}]},
                                                {"name": "post_video_social_actions", "values": [{"value": 7}]}]},
                                      comments=3), {"views": 10, "comments": 3, "shares": 4})
        self.assertEqual(API.parse_fb({"data": [{"name": "post_video_social_actions", "values": [{"value": 7}]}]}),
                         {})                                      # combined, comments unknown: absent
        self.assertEqual(API.parse_ig({"data": [{"name": "views", "total_value": {"value": 12}}]}), {"views": 12})
        self.assertEqual(API.parse_yt({"items": [{"id": "a", "statistics": {"viewCount": "4"},
                                                  "status": {"privacyStatus": "unlisted"}}]}),
                         {"a": {"privacy": "unlisted", "public": False, "views": 4}})
        self.assertIsNone(API.retention({"0": 1}))
        self.assertEqual(len(API.retention({str(i): 100 - i for i in range(300)})), 100)


if __name__ == "__main__":
    unittest.main()
