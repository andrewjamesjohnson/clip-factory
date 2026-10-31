"""Best of GTA (2026-10-29, Andrew: "Gaming is just like everything GTA... they're just the best clips that we can
find. The biggest news, the biggest events, the biggest streamers"; then "Lock it"): the scout's Twitch and Kick clips,
the "best" slots the night deals by size with the three rules, the stream around a clip as the intake, Facebook's cap
keeping the night's best, and the afternoon prep. No network, no skill is run."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-bestofgta-state-")

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import makers as M     # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import scout as SC     # noqa: E402
from clipfactory import timer as T      # noqa: E402

DAY = date(2030, 1, 7)
NOW = datetime(2030, 1, 6, 23, 0, tzinfo=timezone.utc)


def clip(cid: str, who: str, views: int, plat: str = "twitch", game: str = "Grand Theft Auto V", lang="en",
         followers=None, dur: float = 30.0, vod: bool = True, title: str = "", hours: float = 5.0) -> dict:
    cid = cid if plat == "twitch" else f"clip_{cid}"          # Kick's own ids carry clip_ (kick_row)
    return {"platform": plat, "id": cid, "url": f"https://www.twitch.tv/{who}/clip/{cid}" if plat == "twitch"
            else f"https://kick.com/{who}/clips/{cid}", "title": title or f"{who} moment {cid}", "views": views,
            "created_at": (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ"), "duration": dur,
            "game": game, "broadcaster": who, "broadcaster_name": who.title(), "followers": followers,
            "language": lang if plat == "twitch" else None, "vod_id": ("9001" if vod and plat == "twitch" else None),
            "vod_offset": (3600 if vod and plat == "twitch" else None), "vod_title": "NOPIXEL V"}


def row(n: int, kind: str, name: str = "", dur: float = 600, **kw) -> dict:
    out = {"url": f"https://www.youtube.com/watch?v=v{n}", "status": "new", "kind": kind, "score": 100 - n,
           "channel": (name or f"ch{n}").lower(), "name": name or f"Creator {n}", "title": f"upload {n}",
           "duration": dur}
    out.update(kw)
    return out


SCFG = {"topic": r"\b(gta|grand theft)\b", "people": {"xqc": 0.8, "kai cenat": 1.0},
        "drop": {"streamers": [{"name": "xQc", "twitch": "xqc", "kick": "xqc"}, {"name": "Kai Cenat", "twitch": "kaicenat"}]},
        "damp": {"off_topic": {"match": r"\b(gta ?(5|v)|nopixel|rp)\b", "factor": 0.25}}, "half_life_h": 18,
        "streams": {"on": True, "streamers": [{"name": "Sykkuno", "twitch": "sykkuno"}]}}


class StreamFindsTest(unittest.TestCase):
    def rows(self, games, roster=None, skip=(), kfol=None, **kw):
        st = dict(SC.stream_settings(SCFG), **kw)
        return SC.stream_rows(games, roster or {}, SC.stream_faces(SCFG), st, kfol or {}, set(skip), NOW)

    def test_1_faces_are_the_roster_and_the_list_one_entry_a_handle(self):
        names = [f["name"] for f in SC.stream_faces(SCFG)]
        self.assertEqual(names, ["xQc", "Kai Cenat", "Sykkuno"])
        self.assertEqual(SC.stream_settings({}).get("on"), False)          # no block: off

    def test_2_who_counts_language_game_leaks_and_the_floor(self):
        g = [{"game": "Grand Theft Auto V", "clips": [
            clip("a1", "xqc", 6900, followers=12_600_000),                       # a named face
            clip("a2", "burn", 2600, followers=289_000),                         # over the floor: a suggestion
            clip("a3", "nobody", 9000, followers=40_000),                        # under the floor: out
            clip("a4", "jpstreamer", 5900, followers=900_000, lang="ja"),        # not English: out
            clip("a5", "xqc", 900, followers=12_600_000, game="Just Chatting"),  # not GTA: out
            clip("a6", "xqc", 800, followers=12_600_000, title="GTA 6 leak footage"),   # a leak: out
            clip("a7", "creator x", 9000, followers=500_000),                 # blocked: out
        ]}]
        got = {f["id"]: f for f in self.rows(g, skip={"creator x"})}
        self.assertEqual(set(got), {"twitch-a1", "twitch-a2"})
        self.assertEqual(got["twitch-a1"]["given_rights"], "roster")          # fed like a roster upload
        self.assertEqual(got["twitch-a2"]["given_rights"], "stream")          # a suggestion only
        self.assertEqual(got["twitch-a1"]["vod_url"], "https://www.twitch.tv/videos/9001")
        self.assertEqual(got["twitch-a1"]["vod_offset"], 3600.0)
        self.assertEqual((got["twitch-a1"]["kind"], got["twitch-a1"]["roster_kind"]), ("clip", "streamer"))

    def test_3_a_gta_vi_category_before_the_street_date_is_an_early_copy(self):
        g = [{"game": "Grand Theft Auto VI", "clips": [clip("b1", "xqc", 5000, game="Grand Theft Auto VI",
                                                            followers=12_600_000)]}]
        self.assertEqual(self.rows(g, street_date="2031-01-01"), [])
        self.assertEqual(len(self.rows(g, street_date="2030-01-01")), 1)   # from the launch: GTA VI is the game

    def test_4_a_short_clip_needs_its_vod_and_kick_needs_its_follower_count(self):
        g = [{"game": "grand-theft-auto-v", "clips": [
            clip("k1", "buddha", 6000, plat="kick", dur=30),                     # no VOD, 30 s: too short alone
            clip("k2", "buddha", 4000, plat="kick", dur=45),                     # 45 s: kept, if big enough
        ]}]
        self.assertEqual(self.rows(g), [])                                       # Kick says no follower count
        got = self.rows(g, kfol={"kick:buddha": 300_000})
        self.assertEqual([f["id"] for f in got], ["kick-clip_k2"])
        self.assertNotIn("vod_url", got[0])

    def test_5_one_streamer_on_twitch_and_kick_counts_once_two_clips_at_most(self):
        g = [{"game": "Grand Theft Auto V", "clips": [clip("c1", "xqc", 6900, followers=12_600_000),
                                                      clip("c2", "xqc", 4300, followers=12_600_000)]},
             {"game": "grand-theft-auto-v", "clips": [clip("c3", "xqc", 9600, plat="kick", dur=45)]}]
        got = self.rows(g)
        self.assertEqual(sorted(f["id"] for f in got), ["kick-clip_c3", "twitch-c1"])

    def test_6_weighted_by_size_a_named_face_over_a_fresh_small_one(self):
        g = [{"game": "Grand Theft Auto V", "clips": [
            clip("d1", "xqc", 6900, followers=12_600_000, hours=16),
            clip("d2", "acie", 570, followers=325_000, hours=2)]}]
        got = {f["channel_slug"]: f for f in self.rows(g)}
        self.assertEqual(got["twitch:xqc"]["weight"], round(0.8 * 1.6, 3))
        self.assertLess(got["twitch:acie"]["weight"], 0.6)
        for f in got.values():
            f["heat"] = max(SC.heat(f["title"], {})[0], SC.stream_settings(SCFG)["heat_floor"])
            f["rights"], f["derivative"] = f["given_rights"], False
        self.assertGreater(SC.score(got["twitch:xqc"], SCFG), SC.score(got["twitch:acie"], SCFG))

    def test_7_a_clip_is_on_topic_by_category_and_never_damped_as_gta_5(self):
        f = {"kind": "clip", "title": "becoming a jet with 48% jet", "channel": "xQc", "age_h": 5, "heat": 0.5}
        self.assertTrue(SC.keep(f, SCFG, True, 36))
        self.assertFalse(SC.keep(dict(f, kind="video"), SCFG, True, 36))   # an upload still needs its topic
        self.assertEqual(SC.damp(dict(f, title="NoPixel RP: becoming a jet"), SCFG), 1.0)
        self.assertEqual(SC.damp(dict(f, kind="video", title="NoPixel RP: becoming a jet"), SCFG), 0.25)

    def test_7b_a_clip_is_never_breaking_and_has_its_own_table_on_the_board(self):
        f = {"kind": "clip", "age_h": 1.0, "vph": 400000, "title": "x", "rights": "roster"}
        self.assertFalse(SC._breaking(f, mock.Mock(maker="gta"), {}))
        self.assertTrue(SC._breaking(dict(f, kind="video"), mock.Mock(maker="gta"), {}))

    def test_8_the_reads_fail_soft_and_the_kick_counts_are_cached(self):
        acct = mock.Mock(slug="gaming", raw={"gta_maker": {"skip_channels": ["onespot"]}})
        calls = []

        def fetch(args):
            calls.append(args[0])
            if args[0] == "game":
                return {"twitch": [{"game": "Grand Theft Auto V", "clips": [clip("e1", "xqc", 7000,
                                                                            followers=12_600_000)]}],
                        "kick": [{"game": "grand-theft-auto-v", "clips": [clip("e2", "omie", 6500, plat="kick",
                                                                                 dur=60)]}]}
            if args[0] == "roster":
                return {"error": "HTTP 400"}
            if args[0] == "live":
                return {"kick": {"omie": {"followers": 64_000}}}
            return {}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(SC, "scout_dir", return_value=Path(d)):
            rows, err = SC.stream_finds(acct, SCFG, NOW, fetch=fetch)
            self.assertEqual([r["id"] for r in rows], ["twitch-e1"])       # omie is under the floor
            self.assertIn("streamers' clips", err)
            cache = json.loads((Path(d) / "streams.json").read_text())
            self.assertEqual(cache["kick_followers"], {"kick:omie": 64000})
            calls.clear()
            SC.stream_finds(acct, SCFG, NOW, fetch=fetch)
            self.assertNotIn("live", calls)                                  # cached: not asked again
            self.assertNotIn("category", calls)                              # GTA VI looked up once a day


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-bestofgta-"))
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        a = raw["accounts"]["gaming"]
        a["account_dir"] = str(self.tmp / "Gaming")
        (self.tmp / "Gaming" / "Publish").mkdir(parents=True)
        (self.tmp / "Gaming" / "Publish" / "inbox.json").write_text(json.dumps({"items": {}}))
        for other in raw["accounts"].values():           # no other account's inbox is read
            other.setdefault("account_dir", str(self.tmp / "other"))
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("gaming")

    def test_1_a_named_streamer_clip_is_fed_with_its_vod(self):
        f = SC._clip_find(dict(clip("f1", "xqc", 6900, followers=12_600_000), _who={"name": "xQc", "weight": 0.8},
                               _name="xQc", _fol=12_600_000, _vod=True), "twitch:xqc", SC.stream_settings(SCFG), NOW)
        f.update(score=4.2, lane="roster", status="new")
        self.assertEqual(SC.feed_pool([f], {}), [])                         # not the uploads' allowance
        self.assertEqual(SC.stream_pool([f], SCFG), [f])
        with mock.patch.object(SC, "load", return_value={}):
            fed = SC.feed_gta(self.acct, [f], 4)
        self.assertEqual(fed, ["twitch-f1"])
        got = json.loads((self.tmp / "Gaming" / "Publish" / "inbox.json").read_text())["items"]["twitch-f1"]
        self.assertEqual((got["platform"], got["kind"], got["clip"]), ("twitch", "streamer", True))
        self.assertEqual((got["vod_url"], got["vod_offset"]), ("https://www.twitch.tv/videos/9001", 3600.0))
        self.assertEqual(got["name"], "xQc")


class NightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-bestofgta-night-"))
        self._state = C.STATE
        C.STATE = self.tmp / "state"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        a = raw["accounts"]["gaming"]
        a["gta_maker"] = {}
        a["formats"], a["slot_formats"] = ["best"], ["best", "underlay", "pip", "gameplay"]
        a["account_dir"] = str(self.tmp / "Gaming")
        a["delivered"] = [str(self.tmp / "delivered" / "{YYYY}{MM}{DD}")]
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("gaming")
        self.st = M.gta_settings(self.acct)

    def tearDown(self):
        C.STATE = self._state

    def slots(self, n: int) -> list:
        return [{"slot": s, "format": "best", "status": B.OPEN} for s in self.acct.slots_for(DAY)[:n]]

    def test_1_clip_urls_have_ids(self):
        self.assertEqual(M.video_id("https://www.twitch.tv/xqc/clip/DeadOnerousWallaby-4T3ZlN"), "DeadOnerousWallaby-4T3ZlN")
        self.assertEqual(M.video_id("https://clips.twitch.tv/SmellyFrog-abc"), "SmellyFrog-abc")
        self.assertEqual(M.video_id("https://kick.com/xqc/clips/clip_01M62ANQQFWX3QXEC5HYGMFT2H"),
                         "clip_01M62ANQQFWX3QXEC5HYGMFT2H")
        self.assertEqual(M.video_id("https://www.twitch.tv/videos/2913405592"), "2913405592")

    def test_2_a_twitch_clip_is_taken_in_as_the_stream_around_it(self):
        it = {"url": "https://www.twitch.tv/xqc/clip/X", "clip": True, "vod_url": "https://www.twitch.tv/videos/9",
              "vod_offset": 32691, "duration": 60}
        self.assertEqual(M.stream_intake(it, self.st), ("https://www.twitch.tv/videos/9", ["--windows", "32601-32871"]))
        kick = {"url": "https://kick.com/xqc/clips/clip_1", "clip": True, "duration": 45}
        self.assertEqual(M.stream_intake(kick, self.st), ("https://kick.com/xqc/clips/clip_1", []))
        self.assertEqual(M.stream_intake({"url": "u"}, self.st), ("u", []))

    def test_3_the_layout_follows_the_source(self):
        self.assertEqual(M.gta_template(self.acct, "best"), "best")
        self.assertEqual(M.best_template(self.acct, {"kind": "streamer"}, self.st), "gameplay")
        self.assertEqual(M.best_template(self.acct, {"kind": "reaction"}, self.st), "pip")
        for k in ("news", "breakdown", "scout"):
            self.assertEqual(M.best_template(self.acct, {"kind": k}, self.st), "underlay")
        self.assertEqual(M._lanes(self.acct), {"gameplay", "broll"})

    def test_4_the_biggest_first_news_first_two_a_streamer_ranked(self):
        self.acct.clips_per_source = 3          # the mechanics; the live account cuts one clip a video (test_4b)
        src = [row(1, "streamer", "xQc", clip=True, dur=60), row(2, "streamer", "xQc", clip=True, dur=60),
               row(3, "streamer", "xQc", clip=True, dur=60), row(4, "reaction", "Luke Stephens"),
               row(5, "news", "MrBossFTW"), row(6, "breakdown", "TGG", dur=3800)]
        plan, left = M.plan_gta(self.acct, self.slots(6), src)
        by_slot = {s["slot"]: (it["name"], tpl, s["rank"]) for it, ss, tpl in plan for s in ss}
        first = self.acct.slots_for(DAY)[0]
        self.assertEqual(by_slot[first][0], "MrBossFTW")                   # the news first, while it is fresh
        names = [v[0] for v in by_slot.values()]
        self.assertEqual(names.count("xQc"), 2)                              # 2 a streamer
        self.assertEqual(dict((n, t) for n, t, _ in by_slot.values())["xQc"], "gameplay")
        self.assertEqual(dict((n, t) for n, t, _ in by_slot.values())["Luke Stephens"], "pip")
        self.assertEqual(dict((n, t) for n, t, _ in by_slot.values())["TGG"], "underlay")
        self.assertEqual(names.count("TGG"), 2)                              # the long breakdown's second moment
        self.assertEqual(left, [])
        self.assertEqual(sorted(r for _, _, r in by_slot.values())[0], 1)

    def test_4b_live_one_clip_a_video_no_news_quota_biggest_first(self):
        # 2026-10-31 (Andrew: "it's literally just the best clips of the day"): the live account cuts one clip a
        # video and forces no news in or to the front; the biggest posts first
        live = json.loads((ROOT / "config" / "accounts.json").read_text())["accounts"]["gaming"]
        self.acct.clips_per_source = int(live["clips_per_source"])
        self.acct.raw["gta_maker"] = dict(live.get("gta_maker") or {})
        self.assertEqual(live.get("reactions_slots"), [])                   # no slot held for REACTIONS
        src = [row(1, "streamer", "xQc", clip=True, dur=60), row(2, "streamer", "xQc", clip=True, dur=60),
               row(3, "streamer", "xQc", clip=True, dur=60), row(4, "reaction", "Luke Stephens"),
               row(5, "news", "MrBossFTW"), row(6, "breakdown", "TGG", dur=3800)]
        plan, left = M.plan_gta(self.acct, self.slots(6), src)
        by_slot = {s["slot"]: (it["name"], s["rank"]) for it, ss, tpl in plan for s in ss}
        self.assertEqual(by_slot[self.acct.slots_for(DAY)[0]], ("xQc", 1))  # the biggest first, news or not
        names = [n for n, _ in by_slot.values()]
        self.assertEqual(names.count("xQc"), 2)                              # still 2 a streamer
        self.assertEqual(names.count("TGG"), 1)                              # one clip a video
        self.assertEqual(len(left), 1)

    def test_5_the_days_clips_made_count_against_a_streamer(self):
        bd = {"slots": {"07:00": {"status": B.READY, "host": "xQc"}, "10:30": {"status": B.READY, "host": "xQc"}}}
        src = [row(1, "streamer", "xQc", clip=True, dur=60), row(2, "news", "MrBossFTW")]
        plan, left = M.plan_gta(self.acct, self.slots(2), src, bd)
        self.assertEqual([it["name"] for it, _, _ in plan], ["MrBossFTW"])
        self.assertEqual(len(left), 1)

    def test_6_a_clip_never_takes_a_second_slot(self):
        src = [row(1, "streamer", "xQc", clip=True, dur=60)]
        plan, left = M.plan_gta(self.acct, self.slots(3), src)
        self.assertEqual(sum(len(ss) for _, ss, _ in plan), 1)
        self.assertEqual(len(left), 2)

    def test_7_the_old_formats_are_dealt_as_before(self):
        slots = [{"slot": "07:00", "format": "underlay", "status": B.OPEN},
                 {"slot": "10:30", "format": "best", "status": B.OPEN}]
        plan, _ = M.plan_gta(self.acct, slots, [row(1, "news", "A"), row(2, "streamer", "B", clip=True, dur=60)])
        got = {ss[0]["slot"]: tpl for _, ss, tpl in plan}
        self.assertEqual(got["07:00"], "underlay")
        self.assertEqual(got["10:30"], "gameplay")

    def test_8_prep_takes_in_what_the_night_would_and_skips_what_is_done(self):
        src = [row(1, "news", "MrBossFTW", dur=600), row(2, "streamer", "xQc", clip=True, dur=60,
                                                          vod_url="https://www.twitch.tv/videos/9", vod_offset=100)]
        now = datetime(2030, 1, 6, 15, 0, tzinfo=self.cfg.tz())
        ran = []
        with mock.patch.object(M, "gta_sources", return_value=src), \
                mock.patch.object(M, "used_sources", return_value=set()), \
                mock.patch.object(M, "held_elsewhere", return_value=set()), \
                mock.patch.object(M, "killed_ids", return_value=set()), \
                mock.patch.object(M, "run_logged", side_effect=lambda cmd, *a, **k: ran.append(cmd) or
                                  mock.Mock(returncode=0)), \
                mock.patch.object(self.acct, "skill_dir", self.tmp / "skill"):
            (self.tmp / "skill" / "scripts").mkdir(parents=True)
            lines = M.prep_gta(self.cfg, self.acct, DAY, now=now)
            self.assertEqual(len(ran), 2, lines)
            vod = next(c for c in ran if "https://www.twitch.tv/videos/9" in c)
            self.assertEqual(vod[:3], ["nice", "-n", "10"])
            self.assertIn("--windows", vod)
            job = vod[vod.index("--job") + 1]
            (self.tmp / "skill" / "work" / job).mkdir(parents=True)
            for n in ("project.json", "cues.json"):
                (self.tmp / "skill" / "work" / job / n).write_text("{}")
            ran.clear()
            lines = M.prep_gta(self.cfg, self.acct, DAY, now=now)
            self.assertEqual(len(ran), 1)
            self.assertTrue(any("analyzed already" in x for x in lines))
            ran.clear()
            late = datetime(2030, 1, 6, 20, 25, tzinfo=self.cfg.tz())       # a 40-minute intake would end at 21:05
            lines = M.prep_gta(self.cfg, self.acct, DAY, now=late)
            self.assertEqual(ran, [])
            self.assertTrue(any("would end after 20:30" in x for x in lines), lines)


class FacebookBestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-bestofgta-fb-"))
        self._state = C.STATE
        C.STATE = self.tmp / "state"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("gaming")

    def tearDown(self):
        C.STATE = self._state

    def board(self, ranks: dict) -> None:
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"] = {k: {"slot": k, "status": B.SCHEDULED, **({"rank": r} if r else {})}
                           for k, r in ranks.items()}

    def test_1_eight_ranked_slots_keep_the_best_six(self):
        self.board({"07:00": 3, "08:45": 8, "10:30": 1, "12:15": 7, "14:00": 2, "17:30": 4, "19:15": 5, "21:00": 6})
        out = [k for k in ("07:00", "08:45", "10:30", "12:15", "14:00", "17:30", "19:15", "21:00")
               if P.ranked_out(self.acct, DAY, k, 6)]
        self.assertEqual(out, ["08:45", "12:15"])

    def test_2_no_rank_or_no_more_than_the_cap_is_as_before(self):
        self.board({k: None for k in ("07:00", "08:45", "10:30", "12:15", "14:00", "17:30", "19:15", "21:00")})
        self.assertFalse(P.ranked_out(self.acct, DAY, "21:00", 6))          # the Politics: the first six by time
        self.board({"07:00": 2, "10:30": 1, "14:00": 3, "17:30": 4, "19:15": 5, "21:00": 6})
        self.assertFalse(P.ranked_out(self.acct, DAY, "21:00", 6))          # 6 slots, cap 6: every one posts

    def test_3_an_unranked_slot_goes_after_the_ranked_ones(self):
        self.board({"07:00": None, "08:45": 1, "10:30": 2, "12:15": 3, "14:00": 4, "17:30": 5, "19:15": 6,
                    "21:00": 7})
        self.assertTrue(P.ranked_out(self.acct, DAY, "07:00", 6))           # a spare filled by hand: no rank
        self.assertTrue(P.ranked_out(self.acct, DAY, "21:00", 6))
        self.assertFalse(P.ranked_out(self.acct, DAY, "19:15", 6))


class PrepTimerTest(unittest.TestCase):
    def test_1_the_prep_agent_only_while_on(self):
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        tmp = Path(tempfile.mkdtemp(prefix="clipfactory-bestofgta-timer-"))
        p = tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        cfg = C.load(p)
        self.assertEqual(T.prep_agents(cfg), [])
        raw["accounts"]["gaming"].setdefault("gta_maker", {})["prep"] = {"on": True, "at": "15:10"}
        p.write_text(json.dumps(raw))
        cfg = C.load(p)
        got = T.prep_agents(cfg)
        self.assertEqual([(a["name"], a["args"], a["at"]) for a in got],
                         [("gta-prep", ["prep", "gaming", "--quiet"], {"Hour": 15, "Minute": 10})])
        self.assertIn("gta-prep", [a["name"] for a in T.all_agents(cfg)])


if __name__ == "__main__":
    unittest.main()
