import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The social layer's one door out (outward.py), 2026-10-19: queue, check, politics, execute, log. Every step of check()
# in its order, the caps with a seeded jitter, each tripwire and the hold it writes, Stop everything, the politics and
# the lost-answer rules per kind, practice that never calls a platform, testers, the warm-up, quiet hours, YouTube's
# carve-out and units, force on a click, the first comment's same-words exemption, the sender's runners, forget and
# prune. Fake executors and throwaway folders only; nothing can reach the network.

import json
import random
import shutil
import sys
import types
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import capabilities as CAP     # noqa: E402
from clipfactory import config as C             # noqa: E402
from clipfactory import direct as D             # noqa: E402
from clipfactory import outlets as O            # noqa: E402
from clipfactory import outward as OW           # noqa: E402
from clipfactory import registry as REG         # noqa: E402
from clipfactory import social as SOC           # noqa: E402
from clipfactory import store                   # noqa: E402
from clipfactory import switches as SW          # noqa: E402

NOW = datetime(2031, 7, 8, 16, 0, tzinfo=timezone.utc)        # 10:00 in Denver
META_ALL = ["instagram_basic", "instagram_manage_comments", "pages_read_engagement", "pages_read_user_content",
            "pages_manage_engagement", "instagram_manage_messages", "pages_messaging", "pages_manage_metadata",
            "instagram_content_publish", "pages_manage_posts"]


def perr(msg="HTTP 400 from graph.facebook.com: no", **info) -> D.PlatformError:
    info.setdefault("status", 400)
    return D.PlatformError(msg, **info)


class Exe(types.ModuleType):
    """A stand-in executor (firstcomment, engage, cards): what it was asked, and what it answers."""

    def __init__(self, name: str):
        super().__init__("clipfactory." + name)
        self.calls, self.finds, self.states = [], 0, []
        self.fail: list = []
        self.found = None
        self.still = ""

    def execute(self, cfg, acct, action):
        self.calls.append(dict(action))
        if self.fail:
            e = self.fail.pop(0)
            if e is not None:
                raise e
        return {"id": f"R{len(self.calls)}", "url": f"https://x/r{len(self.calls)}"}

    def find(self, cfg, acct, action):
        self.finds += 1
        if isinstance(self.found, BaseException):
            raise self.found
        return self.found

    def still_signed(self, cfg, acct, action):
        return self.still

    def on_state(self, cfg, acct, action, state, result):
        self.states.append(state)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-outward-"))
        self.old_state, C.STATE = C.STATE, self.tmp / "state"
        self.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = self.tmp / "keys"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        raw["review_port"] = 0
        for a in raw["accounts"].values():
            a["delivered"] = [str(self.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["account_dir"] = str(self.tmp / "accounts" / a["name"])
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        REG.reset()
        self.addCleanup(REG.reset)
        D._LAST.usage = {}
        self.addCleanup(setattr, D._LAST, "usage", {})
        OW.RNG.seed(11)
        self.n = 0
        self.exe = {name: self.inject(name, Exe(name)) for name in ("firstcomment", "engage", "cards")}

    def tearDown(self):
        C.STATE = self.old_state
        O.TOKENS, D.TOKENS = self.old_tokens
        shutil.rmtree(self.tmp / "keys", ignore_errors=True)

    def inject(self, name: str, mod):
        key = "clipfactory." + name
        if key in sys.modules:
            self.addCleanup(sys.modules.__setitem__, key, sys.modules[key])
        else:
            self.addCleanup(sys.modules.pop, key, None)
        sys.modules[key] = mod
        REG.reset()
        return mod

    # ------------------------------------------------------------------------------------------------- helpers
    def set(self, modes=None, settings=None, acct=None):
        p = SW.file_path(acct or self.gta)
        raw = store.read(p, {}) or {}
        raw.setdefault("modes", {}).update(modes or {})
        raw.setdefault("settings", {}).update(settings or {})
        store.write(p, raw)
        SW._SEEN.clear()

    def grant(self, acct=None):
        a = acct or self.gta
        CAP.save_scopes(a, "meta", META_ALL, "connect")
        CAP.save_scopes(a, "youtube", ["youtube.force-ssl", "youtube.upload"], "connect")

    def warm(self, acct=None):
        """created long ago, and a factory post on a board long ago: the warm-up is over."""
        a = acct or self.gta
        self.set({}, {"created": "2031-01-01"}, a)
        store.write(store.path(a.slug, "2031-02-01.json"), {"slots": {"07:00": {"outlets": {
            "facebook_reels": {"post_id": "1", "published_at": "2031-02-01T14:00:00+00:00"}}}}})

    def q(self, kind="first_comment", platform="facebook", text=None, manual=False, target=None, key=None,
          now=NOW, **kw) -> str:
        self.n += 1
        signed = {"what": "click", "by": "Andrew"} if manual else {"what": "clip", "by": "Andrew"}
        return OW.queue(self.cfg, self.gta, platform, kind, key=key or f"k:{self.n}",
                        text=f"words number {self.n}" if text is None else text,
                        target=target if target is not None else {"post_id": "P1"}, origin={"by": "test"},
                        signed=signed, now=now, **kw)

    def check(self, kind="first_comment", platform="facebook", manual=False, text="some words", target=None,
              now=NOW, **kw) -> OW.Verdict:
        signed = kw.pop("signed", {"what": "click"} if manual else {"what": "clip"})
        return OW.check(self.cfg, self.gta, platform, kind, origin={"by": "test"}, text=text, target=target or {},
                        manual=manual, signed=signed, now=now, **kw)

    def rows(self, **kw):
        return list(OW.rows(self.cfg, "gaming", **kw))

    def holds(self, acct=None):
        return OW._holds((acct or self.gta).slug)


class CheckStepsTest(Base):
    def test_1_stop_then_start(self):
        self.grant()
        self.set({"first_comment.facebook_reels": "on"})
        self.assertEqual(OW.stopped(self.cfg), "")
        with self.assertRaises(SystemExit):
            OW.stop(self.cfg, "")
        self.assertIn("Stopped by Andrew", OW.stop(self.cfg, "Andrew", "a test"))
        self.assertTrue(OW.stopped(self.cfg).startswith("Stop everything is on (by Andrew at "))
        v = self.check()
        self.assertEqual((v.ok, v.code), (False, "stop"))
        aid = self.q()
        self.assertEqual(OW.drain(self.cfg, NOW), [])                         # nothing goes while stopped
        self.assertEqual(OW.send(self.cfg, self.gta, aid, NOW)["state"], "queued")
        self.assertEqual(self.exe["firstcomment"].calls, [])
        self.assertEqual(OW.health(self.cfg, NOW, [self.gta])[0], "bad")
        self.assertEqual(OW.summary(self.cfg, NOW, [self.gta])[0]["key"], "stop")
        self.assertTrue(OW.morning_lines(self.cfg, NOW)[0].startswith("Stop everything is on"))
        self.assertIn("Started again by Andrew", OW.start(self.cfg, "Andrew"))
        self.assertEqual(OW.start(self.cfg, "Andrew"), "Nothing was stopped.")
        self.assertEqual(len(OW.drain(self.cfg, NOW)), 1)
        self.assertEqual(len(self.exe["firstcomment"].calls), 1)
        # the Log tab's buttons, through social.act: a click on the page only
        with self.assertRaises(SystemExit):
            SOC.act(self.cfg, {"op": "social-stop"}, page=False)
        ok, said = SOC.act(self.cfg, {"op": "social-stop", "why": "lunch"}, page=True)
        self.assertTrue(ok and "Stopped by Andrew" in said)
        self.assertIn("lunch", OW.stopped(self.cfg))
        SOC.act(self.cfg, {"op": "social-start"}, page=True)
        self.assertEqual(OW.stopped(self.cfg), "")

    def test_2_off_refuses_and_practice_never_executes(self):
        v = self.check()
        self.assertEqual((v.ok, v.mode, v.code), (False, "off", "off"))
        aid = self.q()
        self.assertEqual(OW.send(self.cfg, self.gta, aid, NOW)["state"], "skipped")
        self.assertEqual(OW.key_state(self.cfg, self.gta, "k:1"), "skipped")
        self.set({"first_comment.facebook_reels": "practice"})                # no permission read yet: noted
        v = self.check()
        self.assertTrue(v.ok)
        self.assertEqual(v.mode, "practice")
        self.assertIn("would wait for (not read yet: factory.py social probe gaming scopes)", v.why)
        self.q(text="Full video from MrBoss: https://www.youtube.com/watch?v=x")
        lines = OW.drain(self.cfg, NOW)
        self.assertEqual(lines, ["  Gaming facebook first comments: practice, would send: Full video from MrBoss: "
                                 "https://www.youtube.com/watch?v=x"])
        self.assertEqual(self.exe["firstcomment"].calls, [])                   # practice never calls a platform
        row = [r for r in self.rows() if r["state"] == "practice"][0]
        self.assertEqual((row["mode"], row["kind"], row["outlet"]), ("practice", "first_comment", "facebook_reels"))
        self.assertEqual(row["text"], "Full video from MrBoss: https://www.youtube.com/watch?v=x")
        self.assertEqual(OW.key_state(self.cfg, self.gta, "k:2"), "practice")
        self.assertEqual(OW.counts(self.cfg, self.gta, "facebook", "first_comment", 3600, NOW, practice=True), 1)
        self.assertEqual(OW.counts(self.cfg, self.gta, "facebook", "first_comment", 3600, NOW), 0)
        self.assertEqual(self.exe["firstcomment"].states, ["skipped", "practice"])

    def test_3_kind_rules_and_the_youtube_carve_out(self):
        self.grant()
        self.warm()
        self.set({"replies": "on", "automations": "on", "first_comment.youtube_shorts": "on"})
        v = self.check("hide", "instagram")                                     # automated hide: never
        self.assertEqual((v.ok, v.code), (False, "kind"))
        self.assertTrue(self.check("hide", "instagram", manual=True).ok)
        v = self.check("reply_public", "youtube")                               # automated YouTube reply: never
        self.assertEqual((v.ok, v.code), (False, "kind"))
        self.assertIn("YouTube takes nothing automated", v.why)
        v = self.check("first_comment", "youtube", signed={"what": "rule:r-src@v1"})
        self.assertEqual((v.ok, v.code), (False, "kind"))
        v = self.check("first_comment", "youtube")                              # signed with the clip: allowed
        self.assertEqual((v.ok, v.mode), (True, "live"))
        self.assertTrue(self.check("reply_public", "youtube", manual=True).ok)  # a click on YouTube: allowed
        v = self.check("dance", "youtube")
        self.assertEqual(v.code, "kind")

    def test_4_the_permission(self):
        self.set({"first_comment.instagram_reels": "on"})
        CAP.save_scopes(self.gta, "meta", ["instagram_basic", "pages_manage_engagement"], "connect")
        v = self.check("first_comment", "instagram")
        self.assertEqual((v.ok, v.code, v.why), (False, "perm", "waits for instagram_manage_comments"))
        self.assertTrue(self.check("first_comment", "facebook", signed={"what": "clip"}).ok is False)   # off
        self.set({"first_comment.instagram_reels": "practice"})
        v = self.check("first_comment", "instagram")
        self.assertEqual((v.ok, v.mode), (True, "practice"))
        self.assertIn("would wait for instagram_manage_comments", v.why)

    def test_5_messages_reach_only_testers_and_testers_mode(self):
        self.grant()
        self.warm()
        self.set({"automations": "on", "replies": "on"})
        tgt = {"to": "instagram:999", "intent": "asks_source", "at": NOW.isoformat()}
        v = self.check("dm", "instagram", target=tgt)
        self.assertEqual((v.ok, v.mode), (True, "practice"))
        self.assertIn("DMs reach only app testers until Meta's review", v.why)
        v = self.check("dm", "instagram", manual=True, target=tgt)              # a click too: Meta's rule
        self.assertEqual(v.mode, "practice")
        SW.add_tester(self.cfg, self.gta, "instagram", "999", "Andrew", "Andrew")
        SW._SEEN.clear()
        self.assertEqual(self.check("dm", "instagram", target=tgt).mode, "live")
        self.set({}, {"messages_access": "advanced", "testers": []})
        self.assertEqual(self.check("dm", "instagram", target=tgt).mode, "live")
        self.set({"automations": "testers"})
        v = self.check("reply_public", "instagram", target={"to": "instagram:555"})
        self.assertEqual(v.mode, "practice")
        self.assertIn("testers mode: not a tester", v.why)
        SW.add_tester(self.cfg, self.gta, "instagram", "555", "Pat", "Andrew")
        SW._SEEN.clear()
        self.assertEqual(self.check("reply_public", "instagram", target={"to": "instagram:555"}).mode, "live")
        self.assertEqual(self.check("reply_public", "instagram", target={"to": "555"}).mode, "live")
        self.assertEqual(self.check("reply_public", "instagram", manual=True,
                                    target={"to": "instagram:1"}).mode, "live")   # testers mode is for rules only

    def test_6_warm_up(self):
        self.grant()
        self.set({"automations": "on"})
        v = self.check("reply_public", "facebook")
        self.assertEqual(v.mode, "practice")
        self.assertIn("warming up (factory.py social created gaming YYYY-MM-DD", v.why)
        self.set({}, {"created": "2031-07-01"})
        store.write(store.path("gaming", "2031-07-02.json"), {"slots": {"07:00": {"outlets": {
            "facebook_reels": {"post_id": "1", "published_at": "2031-07-02T14:00:00+00:00"}}}}})
        v = self.check("reply_public", "facebook")
        self.assertEqual(v.mode, "practice")
        self.assertIn("warming up until 2031-07-29", v.why)
        self.assertEqual(self.check("reply_public", "facebook", now=NOW + timedelta(days=22)).mode, "live")
        self.set({"first_comment.facebook_reels": "on"})
        self.assertEqual(self.check("first_comment", "facebook").mode, "live")   # a signed first comment: no warm-up

    def test_7_holds_and_the_posters_meta_hold(self):
        self.grant()
        self.set({"first_comment.facebook_reels": "on", "first_comment.instagram_reels": "practice"})
        h = OW.hold(self.cfg, self.gta, "facebook", "comment", 3600, "Meta said 368", code=368, now=NOW)
        self.assertEqual(h["key"], "facebook|comment")
        v = self.check()
        self.assertEqual((v.ok, v.code, v.not_before), (True, "hold", NOW + timedelta(hours=1)))
        self.assertIn("Facebook comments and replies paused until 11:00: Meta said 368", v.why)
        self.assertEqual(OW.held(self.cfg, self.gta, "facebook", "reply_public", NOW),
                         "Facebook comments and replies paused until 11:00: Meta said 368")
        self.assertEqual(OW.held(self.cfg, self.gta, "facebook", "dm", NOW), "")
        OW.hold(self.cfg, self.gta, "instagram", "*", 3600, "too many calls", now=NOW)
        v = self.check("first_comment", "instagram")                            # practice: noted, the row says hold
        self.assertEqual((v.ok, v.mode, v.code, v.not_before), (True, "practice", "hold", None))
        self.assertIn("would wait: Instagram everything social paused", v.why)
        OW.hold(self.cfg, self.gta, "facebook", "comment", 600, "shorter", now=NOW)   # a longer one stays
        self.assertEqual(self.holds()["facebook|comment"]["why"], "Meta said 368")
        O.meta_cool(self.gta, "API access blocked", now=datetime.now(timezone.utc).timestamp())
        v = self.check(now=NOW + timedelta(hours=2))
        self.assertEqual((v.ok, v.code), (True, "hold"))
        self.assertIn("Facebook and Instagram paused, Meta said", v.why)

    def test_8_youtube_units(self):
        self.grant()
        self.set({"first_comment.youtube_shorts": "on"})
        self.assertEqual(OW.yt_left(self.cfg, NOW), 2500)
        OW.yt_spend(None, 2460, "videos.list", False, NOW)                       # graph.py may pass no cfg
        OW.yt_spend(self.cfg, 1, "commentThreads.list", False, NOW)
        d = json.loads((C.STATE / "social" / "youtube-units.json").read_text())
        self.assertEqual((d["day"], d["units"], d["reads"], d["by"]["videos.list"]), ("2031-07-08", 2461, 2, 2460))
        self.assertEqual(OW.yt_left(self.cfg, NOW), 39)
        v = self.check("first_comment", "youtube")
        self.assertEqual((v.ok, v.code), (True, "units"))
        self.assertEqual(v.not_before, datetime(2031, 7, 9, 7, 15, tzinfo=timezone.utc))   # 00:15 Pacific
        self.assertEqual(OW.yt_left(self.cfg, NOW + timedelta(days=1)), 2500)       # a new Pacific day
        OW.yt_spend(self.cfg, "not a number", "x", True, NOW)                      # never raises

    def test_9_windows(self):
        self.grant()
        self.warm()
        self.set({"automations": "on", "cards.story_repost": "on", "first_comment.youtube_shorts": "on"},
                 {"messages_access": "advanced"})
        night = datetime(2031, 7, 8, 9, 0, tzinfo=timezone.utc)               # 03:00 in Denver
        v = self.check("reply_public", "facebook", now=night)
        self.assertEqual((v.code, v.not_before), ("quiet", datetime(2031, 7, 8, 13, 0, tzinfo=timezone.utc)))
        self.assertIsNone(self.check("reply_public", "facebook", manual=True, now=night).not_before)
        early = datetime(2031, 7, 8, 12, 30, tzinfo=timezone.utc)             # 06:30: stories wait for 08:00
        v = self.check("story", "facebook", now=early)
        self.assertEqual(v.not_before, datetime(2031, 7, 8, 14, 0, tzinfo=timezone.utc))
        old = (NOW - timedelta(days=7)).isoformat()
        v = self.check("reply_private", "facebook", target={"at": old})
        self.assertEqual((v.ok, v.code), (False, "window"))
        v = self.check("dm", "instagram", target={"last_in_at": (NOW - timedelta(hours=24)).isoformat()})
        self.assertEqual((v.ok, v.code), (False, "window"))
        self.assertTrue(self.check("dm", "instagram", target={"last_in_at": NOW.isoformat()}).ok)
        public = NOW + timedelta(minutes=40)
        v = self.check("first_comment", "youtube", target={"live_at": public.isoformat()})
        self.assertEqual((v.code, v.not_before), ("window", public))


class CapsTest(Base):
    def test_1_hour_day_force_and_the_seeded_jitter(self):
        self.grant()
        self.set({"replies": "on"})
        for i in range(20):
            OW._bump("gaming", "facebook", "reply_public", True, NOW - timedelta(minutes=50 - i), False)
        v = self.check("reply_public", "facebook", manual=True)
        self.assertEqual((v.ok, v.code), (True, "cap"))
        self.assertEqual(v.not_before, NOW + timedelta(minutes=10))             # the oldest leaves the hour
        self.assertIsNone(self.check("reply_public", "facebook", manual=True, force=True).not_before)
        for i in range(100):
            OW._bump("gaming", "facebook", "reply_public", True, NOW - timedelta(hours=20, minutes=i), False)
        v = self.check("reply_public", "facebook", manual=True)
        self.assertEqual((v.ok, v.code), (False, "cap"))
        self.assertIn("the day's 120 are used; the next can go at", v.why)
        self.assertIn("(or send it anyway)", v.why)
        v = self.check("reply_public", "facebook", manual=True, force=True)
        self.assertTrue(v.ok)
        self.assertNotEqual(v.code, "cap")
        # the spacing: 30 s after a click's reply, plus 0 to 60 s of jitter from the seeded RNG
        shutil.rmtree(C.STATE / "gaming" / "outward")
        OW._bump("gaming", "instagram", "reply_public", True, NOW, False, gap_s=30)
        OW.RNG.seed(5)
        v = self.check("reply_public", "instagram", manual=True)
        self.assertEqual((v.ok, v.code), (True, "gap"))
        self.assertEqual(v.not_before, NOW + timedelta(seconds=30 + random.Random(5).randint(0, 60)))

    def test_2_automated_day_family_engagement_and_instagram_publishes(self):
        self.grant()
        self.warm()
        self.set({"automations": "on", "first_comment.instagram_reels": "on", "cards.story_repost": "on"})
        for i in range(30):
            OW._bump("gaming", "facebook", "reply_public", False, NOW - timedelta(hours=3, minutes=i), False)
        v = self.check("reply_public", "facebook")
        self.assertEqual((v.ok, v.code), (False, "cap"))                       # automated: skipped, no "anyway"
        self.assertIn("30 a day is the most", v.why)
        self.assertFalse(self.check("reply_public", "facebook", force=True).ok)  # force is only for a click
        for i in range(30):                                                     # the comment family: 30 an hour
            OW._bump("gaming", "instagram", "first_comment" if i % 2 else "reply_public", True,
                     NOW - timedelta(minutes=40), False)
        v = self.check("first_comment", "instagram")
        self.assertEqual(v.code, "cap")
        self.assertIn("comments and replies: 30 an hour is the most", v.why)
        for i in range(25):
            OW._bump("gaming", "youtube", "post" if i < 20 else "story", False, NOW - timedelta(hours=5), False)
        self.assertEqual(OW.counts(self.cfg, self.gta, "youtube", "publish", OW.DAY_S, NOW), 25)
        for i in range(25):                                                     # Instagram: 25 publishes a day
            OW._bump("gaming", "instagram", "post", False, NOW - timedelta(hours=5), False)
        v = self.check("story", "instagram")
        self.assertEqual((v.ok, v.code), (False, "cap"))
        self.assertIn("Instagram publishes", v.why)

    def test_3_slow_halves_and_a_reel_keeps_stories_away(self):
        self.grant()
        self.set({"cards.story_repost": "on", "replies": "on"})
        self.assertEqual(OW.note_usage(self.cfg, self.gta, "facebook", {"buc_pct": 80.0}, NOW),
                         "Facebook at 80% of Meta's limit: slower for an hour")
        self.assertTrue(OW.slow(self.cfg, self.gta, "facebook", NOW))
        self.assertFalse(OW.slow(self.cfg, self.gta, "facebook", NOW + timedelta(hours=2)))
        for i in range(10):
            OW._bump("gaming", "facebook", "reply_public", True, NOW - timedelta(minutes=30), False)
        v = self.check("reply_public", "facebook", manual=True)
        self.assertEqual(v.code, "cap")                                         # 20 an hour, halved to 10
        self.assertIn("caps halved", v.why)
        OW._bump("gaming", "facebook", "post", False, NOW - timedelta(minutes=10), False)
        v = self.check("story", "facebook")
        self.assertEqual((v.code, v.not_before), ("gap", NOW + timedelta(minutes=20)))


class TextTest(Base):
    def test_1_words(self):
        self.grant()
        self.warm()
        self.set({"automations": "on", "replies": "on", "first_comment.facebook_reels": "on"},
                 {"messages_access": "advanced"})
        self.assertEqual(self.check("reply_public", "facebook", text="  ").code, "text")
        self.assertEqual(self.check("reply_public", "facebook", text="x" * 8001).code, "text")
        self.assertEqual(self.check("reply_public", "instagram", text="x" * 2201).code, "text")
        self.assertEqual(self.check("dm", "instagram", text="é" * 501, target={"intent": "asks_source"}).code,
                         "text")                                                # 1002 bytes
        v = self.check("reply_public", "facebook", text="It was the hearing — on Tuesday")
        self.assertEqual((v.code, v.why), ("text", "the words have a long dash"))
        self.assertTrue(self.check("reply_public", "facebook", manual=True, text="a – b").ok)   # his own words
        v = self.check("reply_public", "facebook", text="Full one: https://youtu.be/x")
        self.assertEqual(v.code, "text")
        self.assertTrue(self.check("dm", "instagram", text="Full one: https://youtu.be/x",
                                   target={"intent": "asks_full"}).ok)
        self.assertFalse(self.check("dm", "instagram", text="Full one: https://youtu.be/x",
                                    target={"intent": "praise"}).ok)
        OW._bump("gaming", "facebook", "reply_public", False, NOW - timedelta(hours=2), False,
                 text="Thanks for watching")
        v = self.check("reply_public", "facebook", text="thanks  for WATCHING")
        self.assertEqual((v.ok, v.code), (False, "repeat"))
        v = self.check("reply_public", "facebook", manual=True, text="Thanks for watching")
        self.assertEqual((v.ok, v.code), (False, "repeat"))
        self.assertIn("send it anyway", v.why)
        self.assertTrue(self.check("reply_public", "facebook", manual=True, force=True, text="Thanks for watching").ok)
        self.assertTrue(self.check("reply_public", "facebook", text="Thanks for watching",
                                   now=NOW + timedelta(hours=23)).ok)
        OW._bump("gaming", "facebook", "first_comment", False, NOW - timedelta(hours=1), False,
                 text="Full video from MrBoss: https://youtu.be/x")
        v = self.check("first_comment", "facebook", text="Full video from MrBoss: https://youtu.be/x",
                       now=NOW + timedelta(minutes=5))
        self.assertTrue(v.ok)                                                   # the first comment is exempt
        self.assertNotEqual(v.code, "repeat")


_FIND = Exe.find


class SendTest(Base):
    def setUp(self):
        super().setUp()
        self.grant()
        self.warm()
        self.set({"first_comment.facebook_reels": "on", "automations": "on", "replies": "on",
                  "cards.post": "on"}, {"messages_access": "advanced"})

    def test_1_done_is_never_sent_twice(self):
        aid = self.q(key="fc:facebook:V1")
        with self.assertRaises(SystemExit) as ex:
            self.q(key="fc:facebook:V1")
        self.assertIn("already queued", str(ex.exception))
        res = OW.send(self.cfg, self.gta, aid, NOW)
        self.assertEqual((res["state"], res["result"]["id"]), ("done", "R1"))
        self.assertEqual(OW.key_state(self.cfg, self.gta, "fc:facebook:V1"), "done")
        self.assertEqual(OW._items("gaming"), {})
        with self.assertRaises(SystemExit) as ex:
            self.q(key="fc:facebook:V1")
        self.assertIn("already done: it is never sent twice", str(ex.exception))
        self.assertEqual([r["state"] for r in self.rows()], ["queued", "sending", "done"])
        done = self.rows(states=("done",))[0]
        self.assertEqual(done["result"], {"id": "R1", "url": "https://x/r1"})
        self.assertEqual(self.exe["firstcomment"].states, ["done"])
        self.assertEqual(OW.send(self.cfg, self.gta, aid, NOW)["state"], "gone")
        self.assertEqual(len(self.exe["firstcomment"].calls), 1)

    def test_2_a_refusal_is_a_try_and_three_give_up(self):
        self.exe["firstcomment"].fail = [perr("HTTP 400: (#100) Invalid parameter", meta_code=100)] * 3
        aid = self.q()
        t = NOW
        for n in (1, 2):
            res = OW.send(self.cfg, self.gta, aid, t)
            self.assertEqual(res["state"], "failed")
            item = OW._items("gaming")[aid]
            self.assertEqual((item["tries"], item["state"]), (n, "queued"))
            self.assertEqual(OW.key_state(self.cfg, self.gta, "k:1"), "queued")
            self.assertEqual(OW.due(self.cfg, self.gta, t + timedelta(minutes=1)), [])
            t += timedelta(minutes=61)                  # past the hour: three in an hour would be a hold
        res = OW.send(self.cfg, self.gta, aid, t)
        self.assertEqual(res["state"], "gave_up")
        self.assertEqual(OW.key_state(self.cfg, self.gta, "k:1"), "gave_up")
        self.q(key="k:1")                                                       # Send again is allowed then

    def test_3_a_tripwire_holds_and_requeues_not_a_try(self):
        self.exe["firstcomment"].fail = [perr("(#368) The action attempted has been deemed abusive", meta_code=368)]
        aid = self.q()
        res = OW.send(self.cfg, self.gta, aid, NOW)
        self.assertEqual(res["state"], "held")
        item = OW._items("gaming")[aid]
        self.assertEqual((item["tries"], item["state"], item["not_before"]),
                         (0, "queued", (NOW + timedelta(hours=24)).isoformat()))
        h = self.holds()
        self.assertEqual(sorted(h), ["facebook|comment", "facebook|message", "facebook|moderation"])
        self.assertFalse(h["facebook|comment"]["releasable"])
        self.assertEqual(h["facebook|comment"]["code"], 368)
        with self.assertRaises(SystemExit) as ex:
            OW.release(self.cfg, self.gta, "facebook|comment", "Andrew", NOW)
        self.assertIn("cannot be released early", str(ex.exception))
        level, words = OW.health(self.cfg, NOW, [self.gta])
        self.assertEqual(level, "bad")
        self.assertIn("Gaming: Facebook comments and replies paused until Jul 09 10:00: Meta blocked it (368)",
                      words)
        rows = OW.doctor(self.cfg, NOW)
        self.assertIn((False, "Gaming: Facebook comments and replies paused until Jul 09 10:00: Meta blocked it "
                              "(368)", "it ends by itself at Jul 09 10:00"), rows)

    def test_4_permission_gone_and_no_answer(self):
        self.exe["firstcomment"].fail = [perr("(#200) Permissions error", meta_code=200)]
        aid = self.q()
        res = OW.send(self.cfg, self.gta, aid, NOW)
        self.assertEqual(res["state"], "skipped")
        self.assertIn("waits for comment", res["why"])
        self.assertTrue(CAP.missing(self.gta, "facebook", "comment"))
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect")                  # connect again clears it
        self.exe["firstcomment"].fail = [perr("HTTP 404: Object does not exist", status=404)]
        t = NOW + timedelta(minutes=2)                                          # after the 60 s spacing
        self.assertEqual(OW.send(self.cfg, self.gta, self.q(now=t), t)["state"], "skipped")
        self.exe["firstcomment"].fail = [urllib.error.URLError("timed out")]
        t += timedelta(minutes=2)
        aid = self.q(now=t)
        res = OW.send(self.cfg, self.gta, aid, t)
        self.assertEqual(res["state"], "sending")
        self.assertEqual(OW.key_state(self.cfg, self.gta, f"k:{self.n}"), "sending")
        self.assertEqual(OW.due(self.cfg, self.gta, t + timedelta(minutes=5)), [])
        self.assertEqual([i["id"] for i in OW.due(self.cfg, self.gta, t + timedelta(minutes=11))], [aid])

    def test_5_lost_answers_per_kind(self):
        fc = self.exe["firstcomment"]
        fc.fail = [urllib.error.URLError("timed out")]
        aid = self.q()
        OW.send(self.cfg, self.gta, aid, NOW)
        later = NOW + timedelta(minutes=11)
        self.assertEqual(OW.send(self.cfg, self.gta, aid, NOW + timedelta(minutes=3))["state"], "sending")
        fc.found = {"id": "C9", "url": "https://x/c9"}                         # it went after all
        res = OW.send(self.cfg, self.gta, aid, later)
        self.assertEqual((res["state"], res["result"]["id"]), ("done", "C9"))
        self.assertEqual(len(fc.calls), 1)
        # not found: a first comment goes once more, then gives up
        fc.found, fc.fail = None, [urllib.error.URLError("timed out"), None]
        aid = self.q(now=later)
        OW.send(self.cfg, self.gta, aid, later)
        res = OW.send(self.cfg, self.gta, aid, later + timedelta(minutes=11))
        self.assertEqual(res["state"], "queued")
        res = OW.send(self.cfg, self.gta, aid, later + timedelta(minutes=12))
        self.assertEqual(res["state"], "done")
        self.assertEqual(len(fc.calls), 3)
        fc.fail = [urllib.error.URLError("timed out"), urllib.error.URLError("timed out")]
        t = later + timedelta(hours=1)
        aid = self.q(now=t)
        OW.send(self.cfg, self.gta, aid, t)
        OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=11))           # not found: once more
        OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=12))           # lost again
        res = OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=23))
        self.assertEqual(res["state"], "gave_up")
        self.assertIn("Not sure it went: look on Facebook, then press Send again", res["why"])
        # a message is never resent by itself
        en = self.exe["engage"]
        en.fail = [urllib.error.URLError("timed out")]
        aid = self.q("dm", "instagram", manual=True, target={"to": "instagram:1", "thread_id": "T1"})
        OW.send(self.cfg, self.gta, aid, t)
        res = OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=11))
        self.assertEqual(res["state"], "gave_up")
        self.assertIn("look on Instagram", res["why"])
        self.assertEqual(len(en.calls), 1)
        # a look-up that fails three times gives up the same way
        en.fail, en.found = [urllib.error.URLError("timed out")], RuntimeError("Graph is down")
        aid = self.q("reply_public", "instagram", manual=True, target={"comment_id": "c1"})
        OW.send(self.cfg, self.gta, aid, t)
        for n, m in enumerate((11, 22, 33), 1):
            res = OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=m))
        self.assertEqual(res["state"], "gave_up")
        self.assertEqual(en.finds, 4)

    def test_6_still_signed_and_an_executor_not_built(self):
        self.exe["firstcomment"].still = "pulled after it was signed"
        res = OW.send(self.cfg, self.gta, self.q(), NOW)
        self.assertEqual((res["state"], res["why"]), ("skipped", "pulled after it was signed"))
        self.inject("cards", None)
        res = OW.send(self.cfg, self.gta, self.q("card_post", "facebook"), NOW)
        self.assertEqual((res["state"], res["why"]), ("skipped", "the part that sends it is not built yet"))
        self.assertEqual(self.exe["firstcomment"].calls, [])

    def test_7_expired_and_runners(self):
        aid = self.q(expires=NOW + timedelta(minutes=5))
        studio = self.q("card_post", "facebook", runner="studio")
        self.assertEqual([i["id"] for i in OW.due(self.cfg, self.gta, NOW)], [aid])
        self.assertEqual([i["id"] for i in OW.due(self.cfg, self.gta, NOW, runner="studio")], [studio])
        self.assertEqual(OW.drain(self.cfg, NOW + timedelta(minutes=10)),
                         ["  Gaming facebook first comments: skipped, its time passed before it could go"])
        self.assertIn(studio, OW._items("gaming"))                          # the social drain leaves it alone
        self.assertEqual(self.exe["cards"].calls, [])

    def test_8_a_click_with_force(self):
        for i in range(120):
            OW._bump("gaming", "facebook", "reply_public", True, NOW - timedelta(hours=2), False)
        res = OW.send(self.cfg, self.gta, self.q("reply_public", "facebook", manual=True), NOW)
        self.assertEqual(res["state"], "refused")
        res = OW.send(self.cfg, self.gta, self.q("reply_public", "facebook", manual=True, force=True), NOW)
        self.assertEqual(res["state"], "done")
        for i in range(30):
            OW._bump("gaming", "facebook", "reply_public", False, NOW - timedelta(hours=2), False)
        res = OW.send(self.cfg, self.gta, self.q("reply_public", "facebook", force=True,
                                                 target={"to": "facebook:2"}), NOW + timedelta(minutes=5))
        self.assertEqual(res["state"], "skipped")                                # a rule's force means nothing

    def test_9_a_refusal_in_an_executor_hook_never_starves_the_queue(self):
        """still_signed, find and on_state refusing with SystemExit (extras.edit's "It is signed: ...") are that
        action's answer, never the drain's end: the next action still goes."""
        fc = self.exe["firstcomment"]
        refused = "It is signed: Pull it to change its first comment."

        def still(cfg, acct, action):
            if action["key"] == "k:1":
                raise SystemExit(refused)
            return ""

        def on_state(cfg, acct, action, state, result):
            raise SystemExit("not now")
        fc.still_signed, fc.on_state = still, on_state
        self.q()
        self.q(now=NOW + timedelta(seconds=1))
        lines = OW.drain(self.cfg, NOW + timedelta(minutes=1))
        self.assertEqual([c["key"] for c in fc.calls], ["k:2"])
        self.assertEqual(OW.key_state(self.cfg, self.gta, "k:1"), "skipped")
        self.assertEqual(OW.key_state(self.cfg, self.gta, "k:2"), "done")
        self.assertIn(f"  Gaming facebook first comments: skipped, {refused}", lines)
        self.assertEqual(self.rows(states=("skipped",))[0]["why"], refused)
        # a look-up that refuses is a failed look-up, and the drain goes on to the next action
        fc.still_signed = lambda *a: ""
        fc.fail = [urllib.error.URLError("timed out")]
        t = NOW + timedelta(hours=1)
        lost = self.q(now=t)
        OW.send(self.cfg, self.gta, lost, t)
        fc.found = SystemExit("refused")
        nxt = self.q(now=t + timedelta(minutes=11))
        OW.drain(self.cfg, t + timedelta(minutes=12))
        self.assertEqual(OW._items("gaming")[lost]["lookups"], 1)
        self.assertEqual(OW.key_state(self.cfg, self.gta, OW._items("gaming")[lost]["key"]), "sending")
        self.assertNotIn(nxt, OW._items("gaming"))
        self.assertEqual(fc.calls[-1]["id"], nxt)

    def test_10_a_5xx_is_looked_up_never_resent_blind(self):
        """A gateway timeout after Meta made the comment is a lost answer: it stays "sending" and is looked up; a
        message, a story or a card is then never sent again by itself."""
        fc = self.exe["firstcomment"]
        fc.fail = [perr("HTTP 504 from graph.facebook.com: Gateway Timeout", status=504)]
        aid = self.q()
        res = OW.send(self.cfg, self.gta, aid, NOW)
        self.assertEqual(res["state"], "sending")
        self.assertEqual(OW.key_state(self.cfg, self.gta, "k:1"), "sending")
        self.assertEqual(OW._items("gaming")[aid]["tries"], 0)
        self.assertEqual(OW.due(self.cfg, self.gta, NOW + timedelta(minutes=5)), [])
        fc.found = {"id": "C9", "url": "https://x/c9"}                         # Meta had made it
        res = OW.send(self.cfg, self.gta, aid, NOW + timedelta(minutes=11))
        self.assertEqual((res["state"], res["result"]["id"]), ("done", "C9"))
        self.assertEqual((len(fc.calls), fc.finds), (1, 1))
        # an error Meta calls transient is the same; not found, a first comment goes once more after the look-up
        fc.found, fc.fail = None, [perr("HTTP 500: (#2) Service temporarily unavailable", meta_code=2,
                                        transient=True, status=500), None]
        t = NOW + timedelta(hours=1)
        aid = self.q(now=t)
        self.assertEqual(OW.send(self.cfg, self.gta, aid, t)["state"], "sending")
        self.assertEqual(OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=11))["state"], "queued")
        self.assertEqual(OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=12))["state"], "done")
        self.assertEqual((len(fc.calls), fc.finds), (3, 2))
        # a story, a card post and a message: looked up, not found, never sent again by itself
        self.set({"cards.story_repost": "on"})
        for exe, kind, plat, target in ((self.exe["cards"], "story", "facebook", {"card": "c1"}),
                                        (self.exe["cards"], "card_post", "facebook", {"card": "c2"}),
                                        (self.exe["engage"], "dm", "instagram", {"to": "instagram:1",
                                                                                 "thread_id": "T1"})):
            t += timedelta(hours=1)
            exe.fail = [perr("HTTP 502 from graph.facebook.com: Bad Gateway", status=502)]
            before = len(exe.calls)
            aid = self.q(kind, plat, manual=True, target=target, now=t)
            got = OW.send(self.cfg, self.gta, aid, t)
            self.assertEqual(got["state"], "sending", (kind, got.get("why")))
            res = OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=11))
            self.assertEqual(res["state"], "gave_up", kind)
            self.assertIn(f"Not sure it went: look on {OW.PLAT_WORDS[plat]}, then press Send again", res["why"])
            self.assertEqual(len(exe.calls) - before, 1, kind)
        # a refusal made here before any call is a clear no (not a lost answer)
        fc.fail = [D.Refused("the clip changed after signing")]
        t += timedelta(hours=1)
        self.assertEqual(OW.send(self.cfg, self.gta, self.q(now=t), t)["state"], "failed")

    def test_11_a_lost_answer_with_no_find_is_never_resent(self):
        fc = self.exe["firstcomment"]
        del Exe.find                                    # this executor cannot look
        self.addCleanup(setattr, Exe, "find", _FIND)
        fc.fail = [urllib.error.URLError("timed out")]
        aid = self.q()
        OW.send(self.cfg, self.gta, aid, NOW)
        for n, m in enumerate((11, 22), 1):
            res = OW.send(self.cfg, self.gta, aid, NOW + timedelta(minutes=m))
            self.assertEqual(res["state"], "sending")
            self.assertEqual(OW._items("gaming")[aid]["lookups"], n)
        res = OW.send(self.cfg, self.gta, aid, NOW + timedelta(minutes=33))
        self.assertEqual(res["state"], "gave_up")
        self.assertIn("Not sure it went: look on Facebook, then press Send again", res["why"])
        self.assertIn("the part that looks it up is not built yet", res["why"])
        self.assertEqual(len(fc.calls), 1)

    def test_12_words_that_did_not_go_out_are_not_a_repeat(self):
        """The 24-hour same-words check counts words that went out (or may have), never a send that was refused."""
        en = self.exe["engage"]
        target = {"to": "facebook:42", "thread_id": "t1", "last_in_at": NOW.isoformat()}
        en.fail = [perr("HTTP 400: (#100) odd", meta_code=100)]
        aid = self.q("dm", "facebook", text="Here you go", manual=True, target=target)
        self.assertEqual(OW.send(self.cfg, self.gta, aid, NOW)["state"], "failed")
        res = OW.send(self.cfg, self.gta, aid, NOW + timedelta(minutes=6))      # its try again
        self.assertEqual(res["state"], "done", res.get("why"))
        self.assertEqual(len(en.calls), 2)
        # once it went, the same words are a repeat
        t = NOW + timedelta(minutes=10)
        res = OW.send(self.cfg, self.gta, self.q("dm", "facebook", text="here you  GO", manual=True, target=target,
                                                 now=t), t)
        self.assertEqual((res["state"], res["why"]),
                         ("refused", "the same words went out in the last 24 hours (send it anyway, or change them)"))
        # a reply that gave up after three clear noes: Send again with the same words is allowed
        en.fail = [perr("HTTP 400: (#100) odd", meta_code=100)] * 3
        t = NOW + timedelta(minutes=20)
        words = "It is from the 2026 hearing"
        aid = self.q("reply_public", "facebook", text=words, manual=True, target={"comment_id": "c1"}, now=t)
        for m in (0, 61, 122):
            res = OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=m))
        self.assertEqual(res["state"], "gave_up")
        t += timedelta(minutes=130)
        res = OW.send(self.cfg, self.gta, self.q("reply_public", "facebook", text=words, manual=True,
                                                 target={"comment_id": "c1"}, now=t), t)
        self.assertEqual(res["state"], "done", res.get("why"))
        # a reply not found after a lost answer goes once more (it was not there: not a repeat)
        en.fail, en.found = [urllib.error.URLError("timed out"), None], None
        t += timedelta(minutes=5)
        aid = self.q("reply_public", "facebook", text="A new answer", target={"comment_id": "c2", "to": "facebook:7"},
                     now=t)
        OW.send(self.cfg, self.gta, aid, t)
        self.assertEqual(OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=11))["state"], "queued")
        res = OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=12))
        self.assertEqual(res["state"], "done", res.get("why"))
        self.assertFalse(any("text_mark" in r for r in self.rows()))           # the log never carries the mark


    def test_13_not_ready_and_offline_are_waits_never_a_try_nor_a_count(self):
        """2026-10-20 (the builders' reports): direct.Pending (YouTube not public yet, a container still working) and
        net.Offline put the action back in the queue without a try, and the send they began comes off the caps."""
        from clipfactory import net
        fc = self.exe["firstcomment"]

        def counted():
            rec = OW._guard("gaming").get("recent") or {}
            return len(rec.get("facebook|first_comment|auto") or [])
        fc.fail = [D.Pending("the video is still processing"), net.Offline("offline"), None]
        aid = self.q()
        res = OW.send(self.cfg, self.gta, aid, NOW)
        self.assertEqual(res["state"], "queued")
        self.assertIn("not ready yet", res["why"])
        item = OW._items("gaming")[aid]
        self.assertEqual((item.get("tries") or 0, item["waits"], item["state"]), (0, 1, "queued"))
        self.assertEqual(counted(), 0)
        self.assertEqual(OW.send(self.cfg, self.gta, aid, NOW + timedelta(minutes=6))["state"], "queued")
        self.assertEqual((OW._items("gaming")[aid].get("tries") or 0, counted()), (0, 0))
        res = OW.send(self.cfg, self.gta, aid, NOW + timedelta(minutes=12))
        self.assertEqual(res["state"], "done", res.get("why"))
        self.assertEqual(counted(), 1)                                     # only the send that went counts
        self.assertFalse(any("count_mark" in r for r in self.rows()))
        # still not ready after every look: given up, never a blind loop
        fc.fail = [D.Pending("still processing")] * (OW.MAX_WAITS + 1)
        aid = self.q()
        t = NOW + timedelta(hours=1)
        for _ in range(OW.MAX_WAITS + 1):
            res = OW.send(self.cfg, self.gta, aid, t)
            t += timedelta(minutes=6)
        self.assertEqual(res["state"], "gave_up")
        self.assertIn(f"still not ready after {OW.MAX_WAITS} looks", res["why"])

class TripwireTest(Base):
    def trip(self, kind="reply_public", platform="instagram", **info):
        return OW.tripwire(self.cfg, self.gta, platform, kind, perr(**info), NOW)

    def test_1_each_signal(self):
        self.grant()
        h = self.trip(msg="We restrict certain activity to protect our community")
        self.assertEqual(h["keys"], ["instagram|comment", "instagram|message", "instagram|moderation"])
        self.assertEqual(h["until"], (NOW + timedelta(hours=24)).isoformat())
        h = self.trip("story", meta_code=368)
        self.assertEqual((h["key"], h["releasable"], h["until"]),
                         ("instagram|publish", True, (NOW + timedelta(hours=12)).isoformat()))
        shutil.rmtree(C.STATE / "gaming" / "outward")
        h = self.trip(platform="facebook", meta_code=80001)
        self.assertEqual(h["keys"], ["facebook|comment", "facebook|message", "facebook|moderation", "facebook|read"])
        self.assertEqual(h["until"], (NOW + timedelta(hours=1)).isoformat())
        D._LAST.usage = {"regain_min": 20.0, "buc_pct": 100.0}
        h = self.trip(platform="facebook", meta_code=80001)
        self.assertEqual((h["key"], h["releasable"], h["until"]),
                         ("facebook|*", False, (NOW + timedelta(minutes=35)).isoformat()))
        D._LAST.usage = {}
        shutil.rmtree(C.STATE / "gaming" / "outward")
        h = self.trip(meta_code=4)
        self.assertEqual(h["until"], (NOW + timedelta(hours=1)).isoformat())
        h = self.trip(meta_code=17)                                             # a second within a day: 6 hours
        self.assertEqual(h["until"], (NOW + timedelta(hours=6)).isoformat())
        self.assertIn("the second in a day", h["why"])
        for i in range(3):
            OW._bump("gaming", "instagram", "post", False, NOW - timedelta(hours=20 - i), False)
        h = self.trip("post", meta_code=9, meta_subcode=2207042)
        self.assertEqual(h["until"], (NOW + timedelta(hours=4)).isoformat())   # the oldest publish ages out
        got = self.trip("first_comment", meta_code=10)
        self.assertTrue(got["perm"])
        self.assertTrue(any("refused it" in x for x in CAP.missing(self.gta, "instagram", "comment")))
        shutil.rmtree(C.STATE / "gaming" / "outward")
        h = self.trip(meta_code=190)
        self.assertEqual(sorted(self.holds()), ["facebook|comment", "facebook|message", "facebook|moderation",
                                                "facebook|read", "instagram|comment", "instagram|message",
                                                "instagram|moderation", "instagram|read"])
        self.assertIn("factory.py connect meta gaming", h["why"])
        shutil.rmtree(C.STATE / "gaming" / "outward")
        h = self.trip("first_comment", "youtube", msg="HTTP 403: quota", status=403, reason="quotaExceeded")
        self.assertEqual(h["keys"], ["youtube|comment", "youtube|moderation", "youtube|read"])
        self.assertEqual(h["until"], "2031-07-09T07:15:00+00:00")
        h = self.trip("first_comment", "youtube", msg="HTTP 403: slow", status=403, reason="rateLimitExceeded")
        self.assertNotIn("youtube|read", h["keys"])
        self.assertIsNone(self.trip(msg="HTTP 400: odd"))
        self.assertIsNone(self.trip(msg="HTTP 400: odd"))
        h = self.trip(msg="HTTP 400: odd")                                      # the third in an hour
        self.assertEqual((h["key"], h["until"]), ("instagram|reply_public", (NOW + timedelta(hours=6)).isoformat()))
        self.assertIsNone(self.trip(msg="HTTP 500", meta_code=2))               # transient: nothing

    def test_2_note_usage_and_off_means_nothing(self):
        self.assertEqual(OW.note_usage(self.cfg, self.gta, "instagram", {"buc_pct": 95.0}, NOW), "")
        self.assertFalse((C.STATE / "gaming" / "outward").exists())          # every part off: nothing written
        self.set({"guard": "watch"})                                            # watch alone: nothing obeys a hold
        said = OW.note_usage(self.cfg, self.gta, "instagram", {"buc_pct": 95.0}, NOW)
        self.assertIn("slower for an hour", said)
        self.assertIn("Instagram: would pause reading for 60 minutes: Meta's limit at 95%", said)
        said = OW.note_usage(self.cfg, self.gta, "facebook_reels", {"regain_min": 5}, NOW)
        self.assertIn("Facebook: would pause everything social for 30 minutes", said)
        self.assertEqual(self.holds(), {})                                      # 2026-10-20 review: watch writes none
        self.assertEqual([r["why"] for r in self.rows(states=("watch",))],
                         ["would pause reading for 60 minutes: Meta's limit at 95%",
                          "would pause everything social for 30 minutes: Meta asked us to wait 5 minutes (too many "
                          "calls)"])
        self.assertEqual((OW.health(self.cfg, NOW, [self.gta]), OW.morning_lines(self.cfg, NOW)), (("", ""), []))
        self.set({"guard": "on"})
        said = OW.note_usage(self.cfg, self.gta, "instagram", {"buc_pct": 95.0}, NOW)
        self.assertIn("Instagram reading paused until 11:00", said)
        said = OW.note_usage(self.cfg, self.gta, "facebook_reels", {"regain_min": 5}, NOW)
        self.assertIn("Facebook everything social paused until 10:30", said)
        self.assertFalse(self.holds()["facebook|*"]["releasable"])
        self.assertEqual(OW.note_usage(self.cfg, self.gta, "tiktok", {"buc_pct": 99}, NOW), "")

    def test_2c_watch_with_another_part_on_holds_but_never_says_posts(self):
        """2026-10-20 review: with the guard on watch and the checkpoints on, a regain time still holds what obeys it
        (the reads), but the page and the morning notice never say the posts are paused: only the guard holds one."""
        self.set({"guard": "watch", "checkpoints": "on"})
        OW.note_usage(self.cfg, self.gta, "facebook_reels", {"regain_min": 5}, NOW)
        self.assertIn("facebook|*", self.holds())
        self.assertIn("checkpoints", SW.modes(self.gta))
        self.assertEqual(OW.post_gate(self.cfg, self.gta, "facebook_reels", NOW), "")
        self.assertTrue(OW.held(self.cfg, self.gta, "facebook", "read", NOW))
        level, line = OW.health(self.cfg, NOW, [self.gta])
        self.assertEqual(level, "bad")
        self.assertIn("Gaming: Facebook everything social but posts paused until 10:30", line)
        self.assertEqual(OW.morning_lines(self.cfg, NOW), ["Gaming: Facebook everything social but posts paused "
                                                           "until 10:30: Meta asked us to wait 5 minutes (too many "
                                                           "calls)."])
        OW.hold(self.cfg, self.gta, "instagram", "publish", 3600, "Meta blocked publishing (368)", now=NOW)
        self.assertIn("Gaming: Instagram stories and card posts paused until 11:00",
                      OW.health(self.cfg, NOW, [self.gta])[1])
        self.set({"guard": "on"})                                               # on: the posts obey it, and say so
        self.assertIn("Facebook everything social paused until 10:30", OW.health(self.cfg, NOW, [self.gta])[1])

    def test_2b_only_a_platforms_own_numbers_count(self):
        """Meta's usage numbers never become a YouTube hold, and an answer from another host is not Meta's."""
        self.set({"guard": "on"})
        meta = {"regain_min": 20.0, "buc_pct": 95.0, "host": "graph.facebook.com"}
        self.assertEqual(OW.usage_for("youtube_shorts", meta), {})
        self.assertEqual(OW.usage_for("facebook", meta), meta)
        self.assertEqual(OW.usage_for("instagram", {"buc_pct": 80.0}), {"buc_pct": 80.0})       # no host: Meta's
        self.assertEqual(OW.usage_for("instagram", {"buc_pct": 80.0, "host": "evil.example"}), {})
        self.assertEqual(OW.usage_for("youtube", {"retry_after": 30, "host": "www.googleapis.com"}),
                         {"host": "www.googleapis.com", "retry_after": 30})
        self.assertEqual(OW.note_usage(self.cfg, self.gta, "youtube_shorts", meta, NOW), "")
        D._LAST.usage = dict(meta)
        self.assertNotEqual((self.trip("first_comment", "youtube", msg="HTTP 503 from www.googleapis.com",
                                       status=503) or {}).get("kind"), "buc")
        self.assertEqual(OW.post_tripwire(self.cfg, self.gta, "youtube_shorts",
                                          perr("HTTP 503 from www.googleapis.com", status=503), NOW), "")
        self.assertFalse([k for k in self.holds() if k.startswith("youtube|")])
        self.assertFalse(OW.slow(self.cfg, self.gta, "youtube", NOW))
        # the sender forgets an earlier answer's numbers before each call: they are never this action's
        self.grant()
        self.warm()
        self.set({"first_comment.facebook_reels": "on"})
        self.exe["firstcomment"].fail = [perr("HTTP 400: (#100) odd", meta_code=100)]
        D._LAST.usage = {"regain_min": 20.0, "host": "graph.facebook.com"}
        res = OW.send(self.cfg, self.gta, self.q(), NOW)
        self.assertEqual(res["state"], "failed")
        self.assertNotIn("facebook|*", self.holds())

    def test_3_release(self):
        OW.hold(self.cfg, self.gta, "instagram", "publish", 3600, "Instagram's publishing limit", now=NOW)
        with self.assertRaises(SystemExit):
            OW.release(self.cfg, self.gta, "instagram|comment", "Andrew", NOW)
        with self.assertRaises(SystemExit):
            OW.release(self.cfg, self.gta, "instagram|publish", "", NOW)
        self.assertEqual(OW.release(self.cfg, self.gta, "instagram|publish", "Andrew", NOW),
                         "Gaming: instagram|publish released by Andrew")
        self.assertEqual(self.holds(), {})
        d = json.loads((C.STATE / "gaming" / "outward" / "holds.json").read_text())
        self.assertEqual(d["history"][0]["released_by"], "Andrew")


class LogTest(Base):
    def test_1_checklist_counts_practice_rows(self):
        self.grant()
        SW.set_mode(self.cfg, self.gta, "first_comment.facebook_reels", "practice", "Andrew",
                    now=NOW - timedelta(days=3))
        for i in range(10):
            t = NOW - timedelta(days=2, hours=-i)                              # by day: not in quiet hours
            OW.send(self.cfg, self.gta, self.q(now=t), t)
        items = dict((w.split(":")[0], done) for done, w in SW.checklist(self.cfg, self.gta,
                                                                         "first_comment.facebook_reels", NOW))
        self.assertTrue(items["practice for 2 days and 10 rows (or 7 days)"])
        self.assertTrue(items["no practice row over a cap or a hold in the last 2 days"])
        OW.hold(self.cfg, self.gta, "facebook", "comment", 3600, "Meta said 368", now=NOW - timedelta(hours=1))
        OW.send(self.cfg, self.gta, self.q(now=NOW - timedelta(minutes=30)), NOW - timedelta(minutes=30))
        row = self.rows(states=("practice",))[-1]
        self.assertEqual(row["code"], "hold")
        items = dict((w.split(":")[0], done) for done, w in SW.checklist(self.cfg, self.gta,
                                                                         "first_comment.facebook_reels", NOW))
        self.assertFalse(items["no practice row over a cap or a hold in the last 2 days"])

    def test_2_forget(self):
        self.grant()
        self.warm()
        self.set({"replies": "on"}, {"messages_access": "advanced"})
        OW.send(self.cfg, self.gta, self.q("reply_public", "instagram", manual=True, text="hi Pat",
                                           target={"to": "instagram:42", "name": "pat_ig"}), NOW)
        self.q("dm", "instagram", manual=True, text="hello", target={"to": "instagram:42"},
               not_before=NOW + timedelta(hours=1))
        self.q("reply_public", "instagram", manual=True, text="hi Sam", target={"to": "instagram:7"},
               not_before=NOW + timedelta(hours=1))
        n = OW.forget(self.cfg, self.gta, "instagram:42")
        self.assertEqual(n, 5)                                                  # 3 rows, a queued row, the item
        for r in self.rows():
            if r["target"].get("to") == "[forgotten]":
                self.assertIn(r["text"], ("[forgotten]",))
        self.assertNotIn("instagram:42", json.dumps(self.rows()))
        self.assertNotIn("hi Pat", json.dumps(self.rows()))
        self.assertIn("hi Sam", json.dumps(self.rows()))
        self.assertEqual([i["text"] for i in OW._items("gaming").values()], ["hi Sam"])
        with self.assertRaises(SystemExit):
            OW.forget(self.cfg, self.gta, " ")

    def test_3_prune(self):
        self.grant()
        self.set({"first_comment.facebook_reels": "on"})
        old = NOW - timedelta(days=130)
        OW.send(self.cfg, self.gta, self.q(now=old, key="fc:facebook:old"), old)
        mid = NOW - timedelta(days=95)
        OW.send(self.cfg, self.gta, self.q(now=mid, key="fc:facebook:mid", text="Full video: https://y"), mid)
        OW.send(self.cfg, self.gta, self.q(key="fc:facebook:new", text="today's words"), NOW)
        OW.hold(self.cfg, self.gta, "facebook", "publish", 60, "over soon", now=NOW - timedelta(hours=2))
        lines = OW.prune_job(self.cfg, NOW, quiet=True)
        self.assertEqual(lines, ["  Gaming: 1 politics keys past 120 days, 6 log rows past 90 days cleared"])
        self.assertEqual(sorted(OW._politics("gaming")), ["fc:facebook:mid", "fc:facebook:new"])
        rows = self.rows()
        old_rows = [r for r in rows if r["t"] < (NOW - timedelta(days=90)).isoformat()]
        self.assertEqual(len(old_rows), 6)
        self.assertTrue(all(r.get("pruned") and "text" not in r for r in old_rows))
        self.assertIn("today's words", json.dumps(rows))
        self.assertNotIn("https://y", json.dumps(rows))
        self.assertEqual(self.holds(), {})                                      # the ended hold went to history
        self.assertEqual(OW.prune_job(self.cfg, NOW, quiet=True), [])           # once a day

    def test_4_page_lines_with_everything_off(self):
        self.assertEqual(OW.health(self.cfg, NOW, [self.gta, self.politics]), ("", ""))
        self.assertEqual(OW.summary(self.cfg, NOW, [self.gta, self.politics]), [])
        self.assertEqual(OW.morning_lines(self.cfg, NOW), [])
        self.assertEqual(OW.doctor(self.cfg, NOW), [])
        self.assertEqual(OW.drain_job(self.cfg, NOW), [])
        self.assertEqual(OW.post_gate(self.cfg, self.gta, "instagram_reels", NOW), "")
        self.assertEqual(OW.read_paused(self.cfg, self.gta, "instagram_reels", NOW), "")
        self.assertFalse((C.STATE / "gaming" / "outward").exists())
        self.assertEqual(SOC.brief(self.cfg, NOW, "all"), [])
        self.assertEqual(SOC.health_lines(self.cfg, NOW, [self.gta, self.politics]), [])
        self.assertEqual(SOC.morning_lines(self.cfg, NOW), [])

    def test_5_page_lines_with_a_hold(self):
        self.set({"guard": "on"})
        OW.hold(self.cfg, self.gta, "instagram", "publish", 3600, "Instagram's publishing limit (9)", now=NOW)
        self.assertEqual(OW.post_gate(self.cfg, self.gta, "instagram_reels", NOW),
                         "Instagram posts and stories paused until 11:00: Instagram's publishing limit (9)")
        self.assertEqual(OW.post_gate(self.cfg, self.gta, "facebook_reels", NOW), "")
        chips = OW.summary(self.cfg, NOW, [self.gta])
        self.assertEqual([(c["key"], c["words"], c["level"]) for c in chips], [("holds", "1 paused", "bad")])
        self.assertEqual(OW.morning_lines(self.cfg, NOW), ["Gaming: Instagram posts and stories paused until "
                                                           "11:00: Instagram's publishing limit (9)."])
        self.assertIn((False, "Gaming: Instagram posts and stories paused until 11:00: Instagram's publishing "
                              "limit (9)", "factory.py social release gaming instagram|publish --by NAME"),
                      OW.doctor(self.cfg, NOW))
        self.assertIn("Instagram posts and stories paused", SOC.morning_lines(self.cfg, NOW)[0])
        OW.hold(self.cfg, self.gta, "facebook", "read", 3600, "Meta's limit at 95%", now=NOW)
        self.assertEqual(OW.read_paused(self.cfg, self.gta, "facebook_reels", NOW),
                         "Facebook reading paused until 11:00: Meta's limit at 95%")
        self.set({"guard": "watch"})
        self.assertEqual(OW.read_paused(self.cfg, self.gta, "facebook_reels", NOW), "")
        self.assertEqual(OW.post_gate(self.cfg, self.gta, "instagram_reels", NOW), "")



class ReviewFixesTest(Base):
    """2026-10-20, the review of the social layer: a 200 that broke off is looked up, never sent again blind; a hold
    shows only while something obeys it; a cap of 0 is a plain no (never lifted to 1 by slow mode); forget clears the
    person's platform id too, and a forget during the platform call is never written back."""

    def setUp(self):
        super().setUp()
        self.grant()
        self.warm()
        self.set({"first_comment.facebook_reels": "on", "automations": "on", "replies": "on"},
                 {"messages_access": "advanced"})

    def test_1_a_broken_200_is_looked_up_not_sent_again(self):
        import http.client
        broken = (lambda: http.client.IncompleteRead(b"x" * 36, 24),
                  lambda: json.JSONDecodeError("Expecting value", "<html>", 0))
        t = NOW
        for make in broken:
            for kind, plat, target, exe in (
                    ("dm", "instagram", {"to": "instagram:1", "to_id": "1", "thread_id": "T1"}, "engage"),
                    ("first_comment", "facebook", {"post_id": "P1"}, "firstcomment")):
                ex = self.exe[exe]
                ex.calls, ex.finds, ex.found = [], 0, None
                ex.fail = [make()]
                aid = self.q(kind, plat, manual=kind == "dm", target=target, now=t)
                res = OW.send(self.cfg, self.gta, aid, t)
                self.assertEqual(res["state"], "sending", (kind, res))
                self.assertIn("no clear answer", res["why"])
                self.assertEqual(self.rows()[-1]["state"], "lookup")
                self.assertEqual(OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=6))["state"], "sending")
                self.assertEqual(len(ex.calls), 1, kind)                       # not sent again before the look-up
                res = OW.send(self.cfg, self.gta, aid, t + timedelta(minutes=11))
                self.assertEqual(ex.finds, 1, kind)
                if kind == "dm":                                                # a message never goes again by itself
                    self.assertEqual(res["state"], "gave_up")
                    self.assertEqual(len(ex.calls), 1)
                else:                                                           # not found: a first comment, once more
                    self.assertEqual(res["state"], "queued")
                t += timedelta(hours=1)

    def test_2_a_hold_left_after_every_switch_went_off_shows_nowhere(self):
        self.set({"guard": "on"})
        OW.hold(self.cfg, self.gta, "instagram", "publish", 12 * 3600, "Meta blocked publishing (368)", now=NOW)
        self.assertIn("Instagram posts and stories paused", OW.health(self.cfg, NOW, [self.gta])[1])
        self.assertTrue(OW.morning_lines(self.cfg, NOW))
        self.set({k: "off" for k in SW.modes(self.gta)})
        self.assertFalse(SW.any_on(self.gta))
        self.assertEqual(OW.post_gate(self.cfg, self.gta, "instagram_reels", NOW), "")
        self.assertEqual(OW.health(self.cfg, NOW, [self.gta, self.politics]), ("", ""))
        self.assertEqual(OW.summary(self.cfg, NOW, [self.gta, self.politics]), [])
        self.assertEqual(OW.morning_lines(self.cfg, NOW), [])
        self.assertEqual(OW.doctor(self.cfg, NOW), [])
        self.assertIn("instagram|publish", self.holds())                        # kept: it holds again if switched on
        self.set({"guard": "on"})
        self.assertIn("Instagram posts and stories paused", OW.post_gate(self.cfg, self.gta, "instagram_reels", NOW))

    def test_3_a_cap_of_0_is_a_plain_no_even_when_meta_says_slow_down(self):
        for lim in ({"per_day": 0}, {"per_hour": 0}):
            self.set({}, {"caps": {"reply_public/manual": lim}})
            for slow in (False, True):
                with OW._guard_edit("gaming") as g:
                    g["slow_until"] = {"instagram": (NOW + timedelta(hours=1)).timestamp()} if slow else {}
                self.assertEqual(OW.slow(self.cfg, self.gta, "instagram", NOW), slow)
                v = self.check("reply_public", "instagram", manual=True)
                self.assertEqual((v.ok, v.code), (False, "cap"), (lim, slow, v))
                self.assertIn("the account's caps allow none", v.why)
        self.set({}, {"caps": {"reply_public/manual": {"per_day": 1}}})        # 1 stays 1 in slow mode, and goes
        self.assertTrue(self.check("reply_public", "instagram", manual=True).ok)

    def test_4_forget_clears_the_platform_id_and_prune_strips_it(self):
        who = {"to": "instagram:17841499999999999", "to_id": "17841499999999999", "name": "pat_ig",
               "comment_id": "C1"}
        OW.send(self.cfg, self.gta, self.q("reply_public", "instagram", manual=True, text="hi Pat", target=who), NOW)
        self.assertIn("17841499999999999", json.dumps(self.rows()))
        self.assertEqual(OW.forget(self.cfg, self.gta, "instagram:17841499999999999"), 3)
        self.assertNotIn("17841499999999999", json.dumps(self.rows()))
        self.assertTrue(all(r["target"]["to_id"] == "[forgotten]" for r in self.rows()))
        old = NOW - timedelta(days=95)                                          # past 90 days: the id goes too
        OW.send(self.cfg, self.gta, self.q("reply_public", "facebook", manual=True, text="hi Sam", now=old,
                                           target={"to": "facebook:555", "to_id": 555, "comment_id": "C9"}), old)
        OW.prune_job(self.cfg, NOW, quiet=True)
        pruned = [r for r in self.rows() if r.get("pruned")]
        self.assertTrue(pruned)
        self.assertTrue(all("to_id" not in r["target"] and "to" not in r["target"] for r in pruned))
        self.assertEqual({r["target"].get("comment_id") for r in pruned}, {"C9"})

    def test_5_forget_during_the_platform_call_is_never_written_back(self):
        en = self.exe["engage"]
        real = Exe.execute
        person = "instagram:42"

        def execute(cfg, acct, action):
            OW.forget(cfg, acct, person)                                       # Forget pressed while it is out
            return real(en, cfg, acct, action)

        en.execute = execute
        target = {"to": person, "to_id": "42", "name": "pat_ig", "comment_id": "C1"}
        aid = self.q("reply_public", "instagram", manual=True, text="hi Pat, it is from MrBossFTW", target=target)
        res = OW.send(self.cfg, self.gta, aid, NOW)
        self.assertEqual(res["state"], "done")
        log = json.dumps(self.rows())
        for word in ("instagram:42", '"42"', "pat_ig", "MrBossFTW"):
            self.assertNotIn(word, log)
        self.assertEqual(self.rows()[-1]["state"], "done")
        self.assertEqual(self.rows()[-1]["result"]["id"], "R1")
        self.assertIsNone(OW._politics("gaming").get(f"k:{self.n}"))           # the key forget dropped stays dropped
        self.assertEqual(OW._items("gaming"), {})
        self.assertEqual(en.states, [])                                         # nothing more written about them
        # a failure meanwhile: written without them, never queued again for a try or a look-up
        en.fail = [urllib.error.URLError("timed out")]
        aid = self.q("reply_public", "instagram", manual=True, text="hi again Pat", target=target,
                     now=NOW + timedelta(minutes=2))
        res = OW.send(self.cfg, self.gta, aid, NOW + timedelta(minutes=2))
        self.assertEqual(res["state"], "skipped")
        self.assertEqual(OW._items("gaming"), {})
        self.assertNotIn("hi again Pat", json.dumps(self.rows()))


if __name__ == "__main__":
    unittest.main()
