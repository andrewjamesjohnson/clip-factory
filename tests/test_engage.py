import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The Inbox behind the page (engage.py, engage_api.py), 2026-10-20: the poll reads comments only where a post's count
# changed or its decay is due; our own comments are marked ours and never reach the automations, nor does a reply to
# a reply; STOP stops a person; 45 days are kept; forget removes a person; a reading hold, the poster's Meta hold and
# Meta's slow mode skip or stretch the reads; YouTube is read only while units are left and its units are counted;
# messages are read into threads; the executor makes the right call for each kind, and a lost answer is looked up.
# With engage.read off there is no call and no file. Fake Graph and YouTube only (social_fakes); no network.

import io
import json
import sys
import types
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import (Clock, FakeGraph, FakeYouTube, cfg_in_tmp, google_error, meta_error, patch_http,  # noqa: E402
                          write_tokens)

from clipfactory import capabilities as CAP     # noqa: E402
from clipfactory import config as C             # noqa: E402
from clipfactory import direct as D             # noqa: E402
from clipfactory import engage as E             # noqa: E402
from clipfactory import outward as OW           # noqa: E402
from clipfactory import registry as REG         # noqa: E402
from clipfactory import socialstore as SS       # noqa: E402
from clipfactory import store                   # noqa: E402
from clipfactory import switches as SW          # noqa: E402

NOW = datetime(2031, 7, 8, 16, 0, tzinfo=timezone.utc)          # 10:00 in Denver
IG, PAGE, CH = "17841400000000001", "1000001", "UCfakechannel0001"
META_ALL = ["instagram_basic", "instagram_manage_comments", "pages_read_engagement", "pages_read_user_content",
            "pages_manage_engagement", "instagram_manage_messages", "pages_messaging", "pages_manage_metadata"]


def gt(t: datetime) -> str:
    """A time as Graph writes it (+0000)."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def ig_comment(cid, text, who="fan_one", uid="111", at=None, replies=(), hidden=False):
    row = {"id": cid, "text": text, "timestamp": gt(at or NOW - timedelta(minutes=30)), "username": who,
           "from": {"id": uid, "username": who}, "like_count": 1, "hidden": hidden}
    if replies:
        row["replies"] = {"data": list(replies)}
    return row


class Base(unittest.TestCase):
    PLATFORMS = ["instagram"]

    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.clock = Clock(NOW).patch(self, self.cfg)
        write_tokens(self.gta)
        D._LAST.usage = {}
        self.addCleanup(setattr, D._LAST, "usage", {})
        self.g, self.yt = FakeGraph(), FakeYouTube()
        patch_http(self, graph=self.g, yt=self.yt)
        self.seen: list = []
        rules = types.ModuleType("clipfactory.rules")
        rules.on_items = lambda cfg, acct, items, now: (self.seen.extend(items), [f"rules saw {len(items)}"])[1]
        p = mock.patch.dict(sys.modules, {"clipfactory.rules": rules})
        p.start()
        self.addCleanup(p.stop)
        REG.reset()
        self.addCleanup(REG.reset)
        pace = mock.patch.object(E, "PACE_S", 0)
        pace.start()
        self.addCleanup(pace.stop)

    def on(self, acct=None, platforms=None, **modes):
        a = acct or self.gta
        CAP.save_scopes(a, "meta", META_ALL, "connect")
        CAP.save_scopes(a, "youtube", ["youtube.readonly", "youtube.force-ssl", "youtube.upload"], "connect")
        SW.set_mode(self.cfg, a, "engage.read", "on", "Andrew")
        SW.set_setting(self.cfg, a, "engage.platforms", platforms or self.PLATFORMS, "Andrew")
        for k, v in modes.items():
            SW.set_mode(self.cfg, a, k.replace("__", "."), v, "Andrew")

    def media(self, *rows):
        self.g.routes = [r for r in self.g.routes if r["re"].pattern != rf"/{IG}/media$"]      # the new list wins
        self.g.on("GET", rf"/{IG}/media$", {"data": [{"id": m, "comments_count": n, "timestamp": gt(t),
                                                       "permalink": f"https://www.instagram.com/reel/{m}/"}
                                                      for m, n, t in rows]})

    def comments_on(self, mid, rows, times=None):
        self.g.on("GET", rf"^/{mid}/comments$", {"data": list(rows)}, times=times)

    def fetched(self, mid) -> int:
        return sum(1 for c in self.g.calls if c["path"] == f"/{mid}/comments")

    def tick(self, minutes=0):
        if minutes:
            self.clock.advance(minutes=minutes)
        return E.tick(self.cfg, now=self.clock())


class OffTest(Base):
    def test_1_off_means_no_call_and_no_file(self):
        self.media(("M1", 3, NOW - timedelta(hours=2)))
        self.assertEqual(E.tick(self.cfg, now=NOW), [])
        self.assertEqual((self.g.calls, self.yt.calls), ([], []))
        self.assertFalse((C.STATE / "gaming" / "engage").exists())
        self.assertEqual(E.summary(self.cfg, NOW, [self.gta]), [])
        self.assertEqual(E.health(self.cfg, NOW, [self.gta]), ("", ""))
        self.assertEqual(E.morning_line(self.cfg, NOW), "")
        rows = E.doctor(self.cfg, NOW)
        self.assertIn((None, "Gaming inbox: off (factory.py social on gaming engage.read); engage.messages and "
                             "replies wait for it, messages also for Meta's review (Advanced Access)", ""), rows)
        self.assertTrue(all(r[0] is None for r in rows))


class PollTest(Base):
    def test_1_count_diffing(self):
        self.on()
        old = NOW - timedelta(days=3)
        self.media(("M1", 2, old), ("M2", 0, old))
        self.comments_on("M1", [ig_comment("C1", "source?"), ig_comment("C2", "nice one", "fan_two", "222")], times=1)
        self.tick()
        self.assertEqual((self.fetched("M1"), self.fetched("M2")), (1, 0))     # no comments: never read
        self.tick(11)                                                          # same count, 3 days old: hourly
        self.assertEqual(self.fetched("M1"), 1)
        self.g.routes = [r for r in self.g.routes if "media" not in r["re"].pattern]
        self.media(("M1", 3, old), ("M2", 0, old))
        self.comments_on("M1", [ig_comment("C1", "source?"), ig_comment("C2", "nice one", "fan_two", "222"),
                                ig_comment("C3", "part 2 please", "fan_three", "333")])
        self.tick(11)                                                          # the count rose: read now
        self.assertEqual(self.fetched("M1"), 2)
        self.assertEqual(sorted(i["id"] for i in SS.items(self.gta)), ["ig:C1", "ig:C2", "ig:C3"])
        c1 = SS.get_item(self.gta, "ig:C1")
        self.assertEqual((c1["tags"], c1["state"], c1["post"]["platform_id"]), (["question", "asks_source"], "new",
                                                                                 "M1"))
        self.assertEqual(c1["at"], (NOW - timedelta(minutes=30)).isoformat())   # Graph's +0000 read right
        cur = SS.cursor(self.gta, "instagram")
        self.assertEqual(cur["counts"], {"M1": 3, "M2": 0})

    def test_2_decay(self):
        due = E._post_due
        fresh, mid, old = NOW - timedelta(hours=5), NOW - timedelta(days=3), NOW - timedelta(days=10)
        last = NOW - timedelta(minutes=20)
        self.assertFalse(due({"count": 4, "at": gt(fresh)}, 4, last, NOW, 1))         # under 48 hours, count known:
        self.assertTrue(due({"count": 4, "at": gt(fresh)}, 4, NOW - timedelta(minutes=30), NOW, 1))   # 30 minutes
        self.assertTrue(due({"count": None, "at": gt(fresh)}, None, last, NOW, 1))    # no count: every poll
        self.assertFalse(due({"count": 4, "at": gt(mid)}, 4, last, NOW, 1))           # 2 to 7 days: hourly
        self.assertTrue(due({"count": 4, "at": gt(mid)}, 4, NOW - timedelta(minutes=61), NOW, 1))
        self.assertFalse(due({"count": None, "at": gt(old)}, None, NOW - timedelta(hours=5), NOW, 1))   # 6 hours
        self.assertTrue(due({"count": None, "at": gt(old)}, None, NOW - timedelta(hours=6), NOW, 1))
        self.assertFalse(due({"count": None, "at": gt(NOW - timedelta(days=31))}, None, None, NOW, 1))  # past 30
        self.assertTrue(due({"count": 5, "at": gt(mid)}, 4, last, NOW, 1))            # the count changed
        self.assertFalse(due({"count": 4, "at": gt(mid)}, 4, NOW - timedelta(minutes=61), NOW, 4))   # slow: x4
        self.on()
        self.media(("M1", 1, mid))
        self.comments_on("M1", [ig_comment("C1", "hello")])
        self.tick()
        self.tick(30)
        self.assertEqual(self.fetched("M1"), 1)
        self.tick(31)
        self.assertEqual(self.fetched("M1"), 2)

    def test_3_ours_replies_and_the_automations(self):
        self.on()
        mine = {"id": "R2", "text": "It is from MrBossFTW", "timestamp": gt(NOW - timedelta(minutes=10)),
                "username": "gaming", "from": {"id": IG, "username": "gaming"}}
        theirs = {"id": "R1", "text": "@fan_one where is it from?", "timestamp": gt(NOW - timedelta(minutes=20)),
                  "username": "fan_two", "from": {"id": "222", "username": "fan_two"}}
        self.media(("M1", 4, NOW - timedelta(hours=3)))
        self.comments_on("M1", [ig_comment("C1", "source?", replies=[theirs, mine]),
                                ig_comment("C2", "check out my page", "gaming", IG),
                                ig_comment("C3", "who is the streamer", "fan_three", "333")])
        lines = self.tick()
        self.assertIn("rules saw 1", lines)
        self.assertEqual(sorted(i["id"] for i in self.seen), ["ig:C3"])   # never ours, never a reply, and never one
        # our own reply (read back in the same poll) already answered: C1
        self.assertTrue(all(i["kind"] == "comment" and not i["author"]["is_us"] for i in self.seen))
        self.assertTrue(SS.get_item(self.gta, "ig:C2")["author"]["is_us"])
        self.assertEqual(SS.get_item(self.gta, "ig:C2")["suggest"]["hide"], False)   # our own words: no suggestion
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "answered")        # our reply was read back
        self.assertEqual(SS.get_item(self.gta, "ig:R1")["kind"], "reply")
        self.assertEqual(SS.contact(self.gta, "instagram:111")["name"], "fan_one")
        self.assertIsNone(SS.contact(self.gta, f"instagram:{IG}")["first_at"])        # we are not a contact
        self.seen.clear()
        self.tick(11)                                                                  # nothing new: nothing passed
        self.assertEqual(self.seen, [])

    def test_4_stop_and_spam_suggestion(self):
        self.on()
        self.media(("M1", 2, NOW - timedelta(hours=3)))
        self.comments_on("M1", [ig_comment("C1", "STOP replying to me", "grumpy", "444"),
                                ig_comment("C2", "free shark cards at gta-free.xyz", "bot77", "555")])
        self.tick()
        self.assertTrue(SS.stopped(self.gta, "instagram:444"))
        self.assertIn("stop", SS.get_item(self.gta, "ig:C1")["tags"])
        self.assertEqual(SS.get_item(self.gta, "ig:C2")["suggest"]["kind"], "spam")
        self.assertEqual(E.unanswered(self.gta, NOW), (1, 0, 1))
        chips = E.summary(self.cfg, NOW, [self.gta])
        self.assertEqual([c["words"] for c in chips], ["Inbox: 1 to answer", "1 suggested to hide"])
        self.assertEqual(E.morning_line(self.cfg, NOW), "1 comment to answer")

    def test_5_gone_hidden_on_the_phone_and_back(self):
        self.on()
        at = NOW - timedelta(hours=3)
        self.media(("M1", 2, at))
        self.comments_on("M1", [ig_comment("C1", "first"), ig_comment("C2", "second", "fan_two", "222")], times=1)
        self.tick()
        self.media(("M1", 1, at))
        self.comments_on("M1", [ig_comment("C2", "second", "fan_two", "222", hidden=True)], times=1)
        self.tick(11)
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "gone")
        self.assertEqual(SS.get_item(self.gta, "ig:C2")["state"], "hidden")
        self.comments_on("M1", [ig_comment("C2", "second", "fan_two", "222")], times=1)
        self.tick(31)                                                  # the count did not move: the 30-minute read
        self.assertEqual(SS.get_item(self.gta, "ig:C2")["state"], "new")

    def test_6_prune_keeps_45_days_once_a_day(self):
        self.on()
        self.media()
        SS.upsert_items(self.gta, [{"id": "ig:OLD", "platform": "instagram", "kind": "comment", "text": "old",
                                    "at": (NOW - timedelta(days=50)).isoformat(), "author": {"key": "instagram:9"}},
                                   {"id": "ig:NEW", "platform": "instagram", "kind": "comment", "text": "new",
                                    "at": (NOW - timedelta(days=2)).isoformat(), "author": {"key": "instagram:8"}}],
                        NOW)
        lines = self.tick()
        self.assertIn("  Gaming Inbox: 1 records past 45 days removed", lines)
        self.assertEqual([i["id"] for i in SS.items(self.gta)], ["ig:NEW"])
        SS.upsert_items(self.gta, [{"id": "ig:OLD2", "platform": "instagram", "kind": "comment", "text": "x",
                                    "at": (NOW - timedelta(days=60)).isoformat()}], NOW)
        self.tick(11)                                                                  # once a day
        self.assertIsNotNone(SS.get_item(self.gta, "ig:OLD2"))
        SW.set_setting(self.cfg, self.gta, "engage.keep_days", 30, "Andrew")
        self.tick(24 * 60)
        self.assertIsNone(SS.get_item(self.gta, "ig:OLD2"))

    def test_7_forget(self):
        self.on()
        self.media(("M1", 2, NOW - timedelta(hours=3)))
        self.comments_on("M1", [ig_comment("C1", "source?"), ig_comment("C2", "hi", "fan_two", "222")])
        self.tick()
        buf = io.StringIO()
        with redirect_stdout(buf):
            E.cli(self.cfg, ["forget", "gaming", "--person", "instagram:111", "--by", "Andrew"])
        self.assertIn("instagram:111 forgotten", buf.getvalue())
        self.assertIsNone(SS.get_item(self.gta, "ig:C1"))
        self.assertIsNotNone(SS.get_item(self.gta, "ig:C2"))
        self.assertIsNone(SS.contact(self.gta, "instagram:111")["first_at"])

    def test_8_holds_meta_hold_and_slow_mode(self):
        self.on()
        self.media(("M1", 1, NOW - timedelta(hours=3)))
        self.comments_on("M1", [ig_comment("C1", "hi")])
        OW.hold(self.cfg, self.gta, "instagram", "read", 3600, "a test hold", now=NOW)
        lines = self.tick()
        self.assertEqual(self.g.calls, [])
        self.assertIn("paused", SS.cursor(self.gta, "instagram")["waits"])
        self.assertTrue(any("not read" in ln for ln in lines))
        self.clock.advance(minutes=61)
        (C.STATE / "gaming" / "meta-hold.json").write_text(json.dumps({"until": NOW.timestamp() + 7200,
                                                                          "why": "HTTP 400: rate"}))
        self.tick()
        self.assertEqual(self.g.calls, [])                                             # the poster's Meta hold
        (C.STATE / "gaming" / "meta-hold.json").unlink()
        self.tick()
        self.assertEqual(self.fetched("M1"), 1)
        OW.note_usage(self.cfg, self.gta, "instagram", {"buc_pct": 80.0}, self.clock())
        n = len(self.g.calls)
        self.tick(11)
        self.assertEqual(len(self.g.calls), n)                                         # slow: every 40 minutes
        self.tick(30)
        self.assertGreater(len(self.g.calls), n)

    def test_9_a_refusal_writes_the_guards_hold_and_the_doctor_says_so(self):
        self.on()
        self.g.on("GET", rf"/{IG}/media$", meta_error(368, msg="(#368) Temporarily blocked"))
        self.tick()
        self.assertTrue(SS.cursor(self.gta, "instagram")["error"])
        self.assertIn("instagram|comment", dict(OW.active_holds(self.cfg, self.gta, self.clock())))
        level, words = E.health(self.cfg, self.clock(), [self.gta])
        self.assertEqual(level, "bad")
        self.assertIn("the last read failed", words)
        self.assertTrue(any(r[0] is False and "the last read failed" in r[1] for r in E.doctor(self.cfg, NOW)))

    def test_10_missing_permission_waits_and_the_doctor_says_no(self):
        SW.set_mode(self.cfg, self.gta, "engage.read", "on", "Andrew")
        SW.set_setting(self.cfg, self.gta, "engage.platforms", ["instagram"], "Andrew")
        CAP.save_scopes(self.gta, "meta", ["instagram_basic", "pages_read_engagement"], "connect")
        self.tick()
        self.assertEqual(self.g.calls, [])
        self.assertIsNone(E.last_read(self.gta))                                       # nothing was read
        rows = E.doctor(self.cfg, NOW)
        self.assertIn((False, "Gaming inbox on Instagram: on, but the token lacks instagram_manage_comments",
                       "tick them on the system user's token (SETUP-POSTING, comments permissions), then "
                       "factory.py connect meta gaming"), rows)
        self.assertIn((None, "Gaming messages: off; they wait for Meta's review (Advanced Access)", ""), rows)


    def test_11_comments_without_their_words_store_nothing_and_wait(self):
        # 2026-10-20: Instagram answers a token without instagram_manage_comments with each comment's id, timestamp,
        # hidden and like_count, and no words or username: never blank comments by one nameless "instagram:@"
        self.on()
        self.media(("M1", 2, NOW - timedelta(hours=2)))
        self.comments_on("M1", [{"id": "C1", "timestamp": gt(NOW - timedelta(minutes=30)), "hidden": False,
                                 "like_count": 0}, {"id": "C2", "timestamp": gt(NOW - timedelta(minutes=20))}],
                         times=1)
        self.tick()
        self.assertEqual(self.fetched("M1"), 1)
        self.assertEqual((SS.items(self.gta), self.seen), ([], []))
        self.assertIn("the token lacks instagram_manage_comments", SS.cursor(self.gta, "instagram")["error"])
        self.assertIn("instagram refused it", " ".join(CAP.missing(self.gta, "instagram", "read_comments")))
        self.assertTrue(any(r[0] is False and "inbox on Instagram" in r[1] for r in E.doctor(self.cfg, NOW)))
        self.tick(11)
        self.assertEqual(self.fetched("M1"), 1)                                # waits for the next connect
        self.assertIn("refused it", SS.cursor(self.gta, "instagram")["waits"])
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect")                 # the new token
        self.comments_on("M1", [ig_comment("C1", "source?"), ig_comment("C2", "W", "fan_two", "222")])
        self.tick(11)
        self.assertEqual(sorted(i["id"] for i in SS.items(self.gta)), ["ig:C1", "ig:C2"])


class ReviewFixesTest(Base):
    """Review of 2026-10-20: each finding's failure, reproduced on the fakes, now right."""
    LONG_100 = ("(#100) Object with ID 'M_DEL' does not exist, cannot be loaded due to missing permissions, or does "
                "not support this operation")

    def test_1_one_post_that_will_not_read_never_stops_the_others(self):
        self.on()
        self.media(("M_DEL", 2, NOW - timedelta(hours=1)), ("M_OK", 1, NOW - timedelta(days=3)))
        self.g.on("GET", r"^/M_DEL/comments$", meta_error(100, msg=self.LONG_100))
        self.comments_on("M_OK", [ig_comment("C1", "source?")])
        lines = self.tick()
        self.assertEqual((self.fetched("M_DEL"), self.fetched("M_OK")), (1, 1))      # the older post was read
        self.assertIsNotNone(SS.get_item(self.gta, "ig:C1"))
        for _ in range(12):                                                         # two hours of polls
            self.tick(10)
        self.assertEqual(self.fetched("M_DEL"), 1)                                  # it waits 6 hours on its own
        self.assertEqual(OW.active_holds(self.cfg, self.gta, self.clock()), [])     # no hold on the reads
        self.assertEqual(E.health(self.cfg, self.clock(), [self.gta]), ("", ""))    # and no red line
        self.assertFalse(any("could not read" in ln for ln in lines))
        cur = SS.cursor(self.gta, "instagram")
        self.assertIn("does not exist", cur["bad"]["M_DEL"]["why"])
        self.clock.advance(hours=4, minutes=5)
        self.tick()
        self.assertEqual(self.fetched("M_DEL"), 2)                                  # then tried again, once

    def test_2_a_deleted_post_is_dropped_and_its_comments_go(self):
        self.on()
        self.media(("M_GONE", 1, NOW - timedelta(hours=1)))
        self.comments_on("M_GONE", [ig_comment("C1", "hello")], times=1)
        self.tick()
        self.media(("M_GONE", 2, NOW - timedelta(hours=1)))
        self.g.on("GET", r"^/M_GONE/comments$", meta_error(100, msg="(#100) The object does not exist", http=404))
        self.tick(11)
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "gone")
        self.assertTrue(SS.cursor(self.gta, "instagram")["bad"]["M_GONE"]["gone"])
        n = self.fetched("M_GONE")
        for _ in range(6):
            self.tick(60)
        self.assertEqual(self.fetched("M_GONE"), n)                                 # never asked again
        self.assertEqual(E.health(self.cfg, self.clock(), [self.gta]), ("", ""))

    def test_3_a_no_for_every_post_still_stops_and_holds(self):
        self.on()
        self.media(("M1", 1, NOW - timedelta(hours=1)), ("M2", 1, NOW - timedelta(hours=2)))
        self.g.on("GET", r"/comments$", meta_error(4, msg="(#4) Application request limit reached"))
        self.tick()
        self.assertEqual(self.fetched("M1") + self.fetched("M2"), 1)                # the platform said no: stop
        self.assertIn("instagram|read", dict(OW.active_holds(self.cfg, self.gta, self.clock())))
        self.assertEqual(E.health(self.cfg, self.clock(), [self.gta])[0], "bad")

    def test_4_a_hidden_reply_stays_hidden(self):
        from clipfactory import engage_api as API
        self.assertIn("replies{id,text,timestamp,username,from{id,username},like_count,hidden}",
                      API.IG_COMMENT_FIELDS)
        self.on()
        r1 = {"id": "R1", "text": "rude words", "timestamp": gt(NOW - timedelta(minutes=10)), "username": "fan_two",
              "from": {"id": "222", "username": "fan_two"}, "like_count": 0, "hidden": False}
        self.media(("M1", 2, NOW - timedelta(hours=1)))
        self.comments_on("M1", [ig_comment("C1", "first", replies=[r1])])
        self.tick()
        self.assertEqual(self.g.calls[-1]["params"]["fields"], API.IG_COMMENT_FIELDS)
        SS.set_state(self.gta, "ig:R1", "hidden", "click", NOW)                     # the hide went (on_state)
        self.g.routes = [r for r in self.g.routes if "comments" not in r["re"].pattern]
        self.comments_on("M1", [ig_comment("C1", "first", replies=[dict(r1, hidden=True)])])
        self.tick(31)
        it = SS.get_item(self.gta, "ig:R1")
        self.assertEqual((it["state"], it["hidden"]), ("hidden", True))
        no_field = {k: v for k, v in r1.items() if k != "hidden"}                  # an answer without the field
        self.g.routes = [r for r in self.g.routes if "comments" not in r["re"].pattern]
        self.comments_on("M1", [ig_comment("C1", "first", replies=[no_field])])
        self.tick(31)
        it = SS.get_item(self.gta, "ig:R1")
        self.assertEqual((it["state"], it["hidden"]), ("hidden", True))

    def test_5_failing_message_reads_rest_and_never_pause_andrews_messages(self):
        self.on(engage__messages="on", replies="on")
        SW.set_setting(self.cfg, self.gta, "messages_access", "advanced", "Andrew")
        self.media()
        self.g.on("GET", rf"/{PAGE}/conversations$",
                  meta_error(3, msg="(#3) Application does not have the capability to make this API call."))
        for i in range(5):
            self.tick(16 if i else 0)
        convs = sum(1 for c in self.g.calls if c["path"] == f"/{PAGE}/conversations")
        self.assertEqual(convs, 3)                                                  # three, then a 6-hour rest
        self.assertEqual(OW.active_holds(self.cfg, self.gta, self.clock()), [])
        self.assertEqual(OW.held(self.cfg, self.gta, "instagram", "dm", self.clock()), "")
        v = OW.check(self.cfg, self.gta, "instagram", "dm", origin={"by": "Andrew", "via": "page"}, text="hi",
                     target={"to": "instagram:777", "to_id": "777", "last_in_at": self.clock().isoformat()},
                     manual=True, signed={"what": "click", "by": "Andrew", "text_sha": E._sha("hi")},
                     now=self.clock())
        self.assertNotEqual(v.code, "hold")
        self.assertIn("read again at", SS.cursor(self.gta, "instagram")["dm_waits"])
        # a refused permission marks the messages permission, never the comments one
        self.g.routes = [r for r in self.g.routes if "conversations" not in r["re"].pattern]
        self.g.on("GET", rf"/{PAGE}/conversations$", meta_error(10, msg="(#10) Requires pages_messaging"))
        self.clock.advance(hours=7)
        self.tick()
        self.assertTrue(CAP.missing(self.gta, "instagram", "message"))
        self.assertEqual(CAP.missing(self.gta, "instagram", "read_comments"), [])

    def test_6_a_moved_count_on_an_older_post_is_read_at_once(self):
        self.on()
        fresh = [(f"F{i:02d}", 1, NOW - timedelta(hours=1, minutes=i)) for i in range(30)]
        old = ("M_OLD", 3, NOW - timedelta(days=5))
        self.media(*fresh, old)
        self.g.on("GET", r"/comments$", {"data": []})
        self.tick()
        self.tick(11)
        self.tick(11)                                                               # every post read once by now
        n = self.fetched("M_OLD")
        self.media(*fresh, ("M_OLD", 4, NOW - timedelta(days=5)))
        self.tick(10)
        self.assertEqual(self.fetched("M_OLD"), n + 1)                              # the next poll, not 11 hours on
        later = [c["path"] for c in self.g.calls if c["path"].endswith("/comments")][-1:]
        self.assertTrue(later)

    def test_7_overdue_reads_go_before_fresh_ones(self):
        poll = timedelta(minutes=10)
        fresh = {"count": None, "at": gt(NOW - timedelta(hours=2))}
        old = {"count": None, "at": gt(NOW - timedelta(days=20))}
        k_fresh = E._order(fresh, None, NOW - timedelta(minutes=10), NOW, 1, poll)
        k_old = E._order(old, None, NOW - timedelta(hours=9), NOW, 1, poll)
        self.assertLess(k_old, k_fresh)                                             # 9 h of 6 h beats 10 min of 10

    def test_8_a_switched_off_part_leaves_no_red_line(self):
        self.on(engage__messages="on")
        self.media()
        self.g.on("GET", rf"/{PAGE}/conversations$", meta_error(1, msg="(#1) An unknown error occurred"))
        self.tick()
        self.assertEqual(E.health(self.cfg, self.clock(), [self.gta])[0], "bad")
        SW.set_mode(self.cfg, self.gta, "engage.messages", "off", "Andrew")
        for _ in range(3):
            self.tick(60)
        self.assertEqual(E.health(self.cfg, self.clock(), [self.gta]), ("", ""))
        self.assertEqual(E.notes(self.gta), [])
        self.g.on("GET", rf"/{IG}/media$", meta_error(1, msg="(#1) An unknown error occurred"))
        self.g.routes.insert(0, self.g.routes.pop())
        self.tick(60)
        self.assertEqual(E.health(self.cfg, self.clock(), [self.gta])[0], "bad")
        SW.set_setting(self.cfg, self.gta, "engage.platforms", ["youtube"], "Andrew")
        self.assertEqual(E.health(self.cfg, self.clock(), [self.gta]), ("", ""))

    def test_9_conversations_are_paced_and_check_now_skips_unchanged_ones(self):
        self.on(engage__messages="on")
        self.media()
        convs = {"data": [{"id": f"T{i}", "updated_time": gt(NOW)} for i in range(3)] +
                 [{"id": "T9", "updated_time": gt(NOW), "is_owner": False}]}
        self.g.on("GET", rf"/{PAGE}/conversations$",
                  lambda call: convs if call["params"].get("platform") == "instagram" else {"data": []})
        self.g.on("GET", r"^/T\d$", {"messages": {"data": [
            {"id": "m1", "created_time": gt(NOW), "from": {"id": "777", "username": "fan"}, "message": "hi"}]}})
        with mock.patch.object(E, "PACE_S", 0.5), mock.patch.object(E.time, "sleep") as nap:
            self.tick()
        self.assertEqual([c.args for c in nap.call_args_list], [(0.5,)] * 3)        # a rest before each thread
        self.assertNotIn("/T9", [c["path"] for c in self.g.calls])                  # not our app's thread
        n = len(self.g.calls)
        E.check(self.cfg, self.clock() + timedelta(minutes=3))
        self.assertEqual([c["path"] for c in self.g.calls[n:] if c["path"].startswith("/T")], [])

    def test_10_the_automations_never_get_what_our_own_reply_answered(self):
        self.on()
        self.media(("M1", 3, NOW - timedelta(hours=1)))
        ours = {"id": "R9", "text": "From MrBossFTW", "timestamp": gt(NOW - timedelta(minutes=5)),
                "username": "gaming", "from": {"id": IG, "username": "gaming"}}
        self.comments_on("M1", [ig_comment("C1", "source?", replies=[ours]), ig_comment("C2", "who", "b", "2")])
        self.tick()
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "answered")
        self.assertEqual([i["id"] for i in self.seen], ["ig:C2"])


class FacebookTest(Base):
    PLATFORMS = ["facebook"]

    def reels(self, count):
        self.g.routes = [r for r in self.g.routes if "video_reels" not in r["re"].pattern]
        self.g.on("GET", rf"/{PAGE}/video_reels$", {"data": [
            {"id": "V1", "created_time": gt(NOW - timedelta(hours=4)),
             "comments": {"summary": {"total_count": count}}}]})

    def test_1_reels_comments_replies_anonymous_hidden_and_gone(self):
        self.on()
        self.reels(5)
        t = lambda m: gt(NOW - timedelta(minutes=m))       # noqa: E731
        rows = [{"id": "V1_c5", "from": {"id": "903", "name": "Third"}, "message": "agreed",
                 "created_time": t(5), "parent": {"id": "V1_c3"}, "is_hidden": False},
                {"id": "V1_c4", "from": {"id": "902", "name": "Spammer"}, "message": "follow me",
                 "created_time": t(8), "is_hidden": True, "can_reply_privately": True},
                {"id": "V1_c3", "message": "W", "created_time": t(10), "is_hidden": False,
                 "can_reply_privately": False},
                {"id": "V1_c2", "from": {"id": PAGE, "name": "Gaming"}, "message": "From MrBossFTW",
                 "created_time": t(12), "parent": {"id": "V1_c1"}, "is_hidden": False},
                {"id": "V1_c1", "from": {"id": "901", "name": "Fan One"}, "message": "where is this from?",
                 "created_time": t(20), "is_hidden": False, "can_reply_privately": True, "like_count": 3}]
        self.g.on("GET", r"^/V1/comments$", {"data": rows}, times=1)
        self.tick()
        call = [c for c in self.g.calls if c["path"] == "/V1/comments"][0]
        self.assertEqual((call["params"]["filter"], call["params"]["order"]), ("stream", "reverse_chronological"))
        self.assertIn("parent{id}", call["params"]["fields"])
        reel = [c for c in self.g.calls if c["path"] == f"/{PAGE}/video_reels"][0]
        self.assertIn("comments.summary(true)", reel["params"]["fields"])
        c1, c2, c3 = (SS.get_item(self.gta, f"fb:V1_c{i}") for i in (1, 2, 3))
        self.assertEqual((c1["state"], c1["can_reply_privately"], c1["like_count"]), ("answered", True, 3))
        self.assertTrue(c2["author"]["is_us"])
        self.assertEqual((c3["author"]["key"], c3["author"]["name"]), ("facebook:anon-V1_c3", "Someone on Facebook"))
        self.assertTrue(SS.get_item(self.gta, "fb:V1_c4")["hidden"])                # hidden there: not to answer
        self.assertEqual(E.unanswered(self.gta, self.clock())[0], 2)                 # c3 and c5 (c1 is answered)
        self.assertEqual((SS.get_item(self.gta, "fb:V1_c5")["kind"], SS.get_item(self.gta, "fb:V1_c5")["parent_id"]),
                         ("reply", "V1_c3"))
        self.assertEqual([i["id"] for i in self.seen], ["fb:V1_c3"])               # not ours, a reply, hidden, answered
        self.reels(4)
        self.g.on("GET", r"^/V1/comments$", {"data": rows[1:]}, times=1)
        self.tick(11)
        self.assertEqual(SS.get_item(self.gta, "fb:V1_c5")["state"], "gone")        # a reply deleted on Facebook
        self.assertEqual(SS.get_item(self.gta, "fb:V1_c1")["state"], "answered")


    def test_2_a_reel_whose_comments_are_refused_or_stripped_stores_nothing(self):
        # 2026-10-20: without pages_read_user_content Facebook refuses a reel that has comments ((#200) Missing
        # Permissions); an answer without the words and names is the same no
        self.on()
        self.reels(3)
        self.g.on("GET", r"^/V1/comments$", meta_error(200, msg="(#200) Missing Permissions"), times=1)
        self.tick()
        self.assertEqual(SS.items(self.gta), [])
        self.assertIn("facebook refused it", " ".join(CAP.missing(self.gta, "facebook", "read_comments")))
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect")
        self.g.on("GET", r"^/V1/comments$", {"data": [{"id": "V1_c1", "created_time": gt(NOW), "is_hidden": False}]},
                  times=1)
        self.tick(11)
        self.assertEqual(SS.items(self.gta), [])
        self.assertIn("the token lacks pages_read_user_content", SS.cursor(self.gta, "facebook")["error"])
        self.assertIn("facebook refused it", " ".join(CAP.missing(self.gta, "facebook", "read_comments")))


class YouTubeTest(Base):
    PLATFORMS = ["youtube"]

    def thread(self, tid, text, who="Viewer", cid="UCviewer", vid="ytvid1", replies=()):
        return {"id": tid, "snippet": {"videoId": vid, "canReply": True, "topLevelComment": {"id": tid, "snippet": {
            "videoId": vid, "textOriginal": text, "authorDisplayName": who, "authorChannelId": {"value": cid},
            "publishedAt": (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"), "likeCount": 2}}},
            "replies": {"comments": list(replies)}}

    def test_1_threads_read_and_units_counted(self):
        self.on()
        ours = {"id": "Y1.r", "snippet": {"textOriginal": "thanks", "authorDisplayName": "Gaming",
                                          "authorChannelId": {"value": CH}, "publishedAt": NOW.isoformat()}}
        self.yt.on("GET", r"/commentThreads$", {"items": [self.thread("Y1", "when does gta 6 come out", replies=[ours]),
                                                          self.thread("Y2", "W", "Fan", "UCfan")]})
        self.tick()
        self.assertEqual(self.yt.calls[0]["params"]["allThreadsRelatedToChannelId"], CH)
        self.assertEqual(self.yt.calls[0]["params"]["order"], "time")
        units = store.read(store.path("social", "youtube-units.json"), {})
        self.assertEqual((units["units"], units["by"]), (1, {"commentThreads.list": 1}))
        y1 = SS.get_item(self.gta, "yt:Y1")
        self.assertEqual((y1["tags"], y1["state"], y1["post"]["url"]),
                         (["question", "asks_when"], "answered", "https://www.youtube.com/shorts/ytvid1"))
        self.assertTrue(SS.get_item(self.gta, "yt:Y1.r")["author"]["is_us"])
        self.tick(5)                                                                   # every 15 minutes
        self.assertEqual(len(self.yt.calls), 1)
        self.tick(11)
        self.assertEqual(len(self.yt.calls), 2)

    def test_3_a_comment_deleted_on_youtube_becomes_gone_after_a_complete_read(self):
        self.on()
        self.yt.on("GET", r"/commentThreads$", {"items": [self.thread("Y1", "first"), self.thread("Y2", "second"),
                                                          self.thread("Y3", "third")]}, times=1)
        self.tick()
        SS.set_state(self.gta, "yt:Y3", "hidden", "click", NOW)                     # held for review by us
        self.yt.on("GET", r"/commentThreads$", {"items": [self.thread("Y2", "second")], "nextPageToken": "p2"},
                   times=1)
        self.tick(16)                                                               # not every thread: nothing goes
        self.assertEqual(SS.get_item(self.gta, "yt:Y1")["state"], "new")
        self.yt.on("GET", r"/commentThreads$", {"items": [self.thread("Y2", "second")]}, times=1)
        self.tick(16)                                                               # the channel's every thread
        self.assertEqual(SS.get_item(self.gta, "yt:Y1")["state"], "gone")
        self.assertEqual(SS.get_item(self.gta, "yt:Y2")["state"], "new")
        self.assertEqual(SS.get_item(self.gta, "yt:Y3")["state"], "hidden")         # held is not deleted

    def test_4_readonly_is_not_enough_and_a_scope_refusal_marks_it(self):
        # 2026-10-20: commentThreads.list refuses a youtube.readonly token, 403 "insufficient authentication scopes"
        self.on()
        CAP.save_scopes(self.gta, "youtube", ["youtube.readonly", "youtube.upload"], "connect")
        self.yt.on("GET", r"/commentThreads$", {"items": []})
        self.tick()
        self.assertEqual(self.yt.calls, [])                                        # waits, never asks
        self.assertEqual(SS.cursor(self.gta, "youtube")["waits"], "waits for youtube.force-ssl")
        self.assertIn((False, "Gaming inbox on YouTube: on, but the token lacks youtube.force-ssl",
                       "factory.py connect youtube gaming --client client.json --with comments"),
                      E.doctor(self.cfg, NOW))
        # a token that says force-ssl and is refused anyway: the need waits for the next connect
        CAP.save_scopes(self.gta, "youtube", ["youtube.force-ssl", "youtube.upload"], "connect")
        self.yt.routes = []
        self.yt.on("GET", r"/commentThreads$", google_error(
            "insufficientPermissions", msg="Request had insufficient authentication scopes."))
        self.tick(16)
        self.assertEqual(len(self.yt.calls), 1)
        self.assertIn("insufficient authentication scopes", SS.cursor(self.gta, "youtube")["error"])
        self.assertIn("youtube refused it", " ".join(CAP.missing(self.gta, "youtube", "read_comments")))
        self.tick(16)
        self.assertEqual(len(self.yt.calls), 1)

    def test_2_no_read_with_few_units_left(self):
        self.on()
        self.yt.on("GET", r"/commentThreads$", {"items": []})
        for _ in range(3):
            OW.yt_spend(self.cfg, 800, "comments.insert", True, NOW)
        self.assertLessEqual(OW.yt_left(self.cfg, NOW), E.YT_MIN_UNITS)
        self.tick()
        self.assertEqual(self.yt.calls, [])
        self.assertIn("units", SS.cursor(self.gta, "youtube")["waits"])


class MessagesTest(Base):
    def test_1_conversations_threads_and_stop(self):
        self.on(engage__messages="on")
        self.media()
        self.g.on("GET", rf"/{PAGE}/conversations$", lambda call: {"data": [
            {"id": "T1", "updated_time": gt(NOW)}]} if call["params"].get("platform") == "instagram" else {"data": []})
        self.g.on("GET", r"^/T1$", {"messages": {"data": [
            {"id": "m2", "created_time": gt(NOW - timedelta(minutes=2)), "from": {"id": "777", "username": "fan_dm"},
             "to": {"data": [{"id": IG}]}, "message": "stop"},
            {"id": "m1", "created_time": gt(NOW - timedelta(minutes=9)), "from": {"id": IG, "username": "gaming"},
             "to": {"data": [{"id": "777", "username": "fan_dm"}]}, "message": "hey"}]}})
        lines = self.tick()
        t = SS.thread(self.gta, "igt:T1")
        self.assertEqual((t["with"]["key"], t["unread"], len(t["messages"])), ("instagram:777", 1, 2))
        self.assertEqual(t["window_until"], (NOW - timedelta(minutes=2) + timedelta(hours=24)).isoformat())
        self.assertTrue(SS.stopped(self.gta, "instagram:777"))
        self.assertEqual([i["id"] for i in self.seen], ["igm:m2"])
        self.assertIn("rules saw 1", lines)
        n = len(self.g.calls)
        self.tick(16)                                                                  # updated_time the same
        self.assertEqual([c["path"] for c in self.g.calls[n:] if c["path"] == "/T1"], [])


class ExecutorTest(Base):
    def setUp(self):
        super().setUp()
        self.on(replies="on")
        SW.set_setting(self.cfg, self.gta, "messages_access", "advanced", "Andrew")
        SS.upsert_items(self.gta, [
            {"id": "ig:C1", "platform": "instagram", "kind": "comment", "platform_comment_id": "C1", "text": "source?",
             "at": (NOW - timedelta(hours=1)).isoformat(), "author": {"key": "instagram:111", "id": "111",
                                                                      "name": "fan_one", "is_us": False},
             "post": {"platform_id": "M1"}}], NOW)

    def queue(self, kind, platform="instagram", text="", **target):
        t = {"comment_id": "C1", "item": "ig:C1", "to": "instagram:111", "to_id": "111",
             "at": (NOW - timedelta(hours=1)).isoformat(), "last_in_at": (NOW - timedelta(hours=1)).isoformat()}
        t.update(target)
        return OW.queue(self.cfg, self.gta, platform, kind, key=f"k:{kind}:{platform}:{text}", text=text, target=t,
                        origin={"by": "Andrew", "via": "page"},
                        signed={"what": "click", "by": "Andrew", "text_sha": E._sha(text)}, now=NOW)

    def send(self, aid, now=NOW):
        return OW.send(self.cfg, self.gta, aid, now)

    def test_1_each_kind_makes_its_call(self):
        self.g.on("POST", r"^/C1/replies$", {"id": "R9"})
        self.g.on("POST", r"^/C1$", {"success": True})
        self.g.on("POST", rf"^/{PAGE}/messages$", {"recipient_id": "111", "message_id": "mid.1"})
        self.g.on("POST", r"^/F1/comments$", {"id": "F1_9"})
        self.yt.on("POST", r"/comments$", {"id": "Y1.9"})
        self.yt.on("POST", r"/comments/setModerationStatus$", {})
        got = self.send(self.queue("reply_public", text="From MrBossFTW's stream"))
        self.assertEqual(got["state"], "done")
        self.assertEqual(self.g.calls[-1]["form"]["message"], "From MrBossFTW's stream")
        it = SS.get_item(self.gta, "ig:C1")
        self.assertEqual(it["state"], "answered")
        self.assertEqual([r["state"] for r in it["replies"]], ["done"])
        self.send(self.queue("reply_private", text="Here is the full stream"), NOW + timedelta(minutes=1))
        f = self.g.calls[-1]["form"]
        self.assertEqual((json.loads(f["recipient"]), json.loads(f["message"])),
                         ({"comment_id": "C1"}, {"text": "Here is the full stream"}))
        self.assertEqual(SS.contact(self.gta, "instagram:111")["private_replied"], ["C1"])
        self.send(self.queue("dm", text="Thanks for watching", item="", thread="igt:T1"), NOW + timedelta(minutes=2))
        f = self.g.calls[-1]["form"]
        self.assertEqual((json.loads(f["recipient"]), f["messaging_type"]), ({"id": "111"}, "RESPONSE"))
        self.send(self.queue("hide", text=""), NOW + timedelta(minutes=3))
        self.assertEqual(self.g.calls[-1]["form"]["hide"], "true")
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "hidden")
        self.send(OW.queue(self.cfg, self.gta, "facebook", "hide", key="k:fbhide", text="",
                           target={"comment_id": "F1", "item": ""}, origin={"by": "Andrew"},
                           signed={"what": "click", "by": "Andrew"}, now=NOW), NOW + timedelta(minutes=4))
        self.assertEqual(self.g.calls[-1]["form"]["is_hidden"], "true")
        self.send(self.queue("reply_public", "youtube", "Thanks", comment_id="Y1", item=""), NOW + timedelta(minutes=5))
        self.assertEqual(self.yt.calls[-1]["body"], {"snippet": {"parentId": "Y1", "textOriginal": "Thanks"}})
        self.send(self.queue("yt_moderate", "youtube", "", comment_id="Y2", item="", status="heldForReview"),
                  NOW + timedelta(minutes=6))
        self.assertEqual(self.yt.calls[-1]["params"]["moderationStatus"], "heldForReview")
        self.assertNotIn("banAuthor", self.yt.calls[-1]["params"])
        self.assertTrue(all(c["method"] != "DELETE" for c in self.g.calls + self.yt.calls))

    def test_2_lost_answers(self):
        self.g.on("POST", r"^/C1/replies$", urllib.error.URLError("timed out"))
        aid = self.queue("reply_public", text="It is from the GTA RP server")
        self.assertEqual(self.send(aid)["state"], "sending")
        self.g.on("GET", r"^/C1/replies$", {"data": [{"id": "R7", "text": "It is from the GTA RP server",
                                                       "from": {"id": IG}}]})
        got = self.send(aid, NOW + timedelta(minutes=11))
        self.assertEqual((got["state"], got["result"]["id"]), ("done", "R7"))
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "answered")
        self.g.on("POST", rf"^/{PAGE}/messages$", urllib.error.URLError("timed out"))
        self.g.on("GET", rf"^/{PAGE}/conversations$", {"data": [{"messages": {"data": [
            {"id": "x", "message": "something else", "from": {"id": IG}}]}}]})
        aid = self.queue("dm", text="See you in part 2", item="", thread="igt:T1")
        self.send(aid, NOW + timedelta(minutes=12))
        got = self.send(aid, NOW + timedelta(minutes=23))
        self.assertEqual(got["state"], "gave_up")
        self.assertIn("Not sure it went", got["why"])
        self.assertEqual(self.g.calls[-1]["params"]["user_id"], "111")

    def test_3_still_signed(self):
        aid = self.queue("reply_public", text="the words clicked")
        q = store.read(store.path("gaming", "outward", "queue.json"), {})
        q["items"][aid]["text"] = "other words"
        store.write(store.path("gaming", "outward", "queue.json"), q)
        got = self.send(aid)
        self.assertEqual(got["state"], "refused")
        self.assertIn("not the words that were clicked", got["why"])
        self.assertEqual(self.g.calls, [])
        self.assertIn("not the words", SS.get_item(self.gta, "ig:C1")["replies"][-1]["why"])
        action = {"kind": "reply_public", "signed": {"what": "rule:r-src@v2"}, "target": {"to": "instagram:111"}}
        self.assertIn("not built here", E.still_signed(self.cfg, self.gta, action))
        SS.stop_contact(self.gta, "instagram:111", NOW)
        self.assertIn("STOP", E.still_signed(self.cfg, self.gta, action))
        self.assertIn("not signed", E.still_signed(self.cfg, self.gta, {"kind": "hide", "signed": {"what": "clip"}}))

    def test_4_a_refusal_is_recorded_on_the_reply(self):
        self.g.on("POST", r"^/C1/replies$", meta_error(100, msg="(#100) The comment does not exist", http=404))
        got = self.send(self.queue("reply_public", text="hello there"))
        self.assertEqual(got["state"], "refused")
        r = SS.get_item(self.gta, "ig:C1")["replies"][-1]
        self.assertEqual(r["state"], "refused")
        self.assertTrue(r["why"])


class PostsIndexTest(Base):
    def test_1_boards_and_pulled_posts(self):
        day = NOW.astimezone(self.cfg.tz()).date()
        board = {"slots": {"07:00": {"slot": "07:00", "publish_at": f"{day}T07:00:00-06:00", "slug": "the-chase",
                                     "source_title": "The Chase", "cover": "/tmp/x.jpg",
                                     "outlets": {"instagram_reels": {"post_id": "M7", "url": "https://ig/M7",
                                                                     "published_at": gt(NOW - timedelta(hours=3))}}}}}
        store.write(store.path("gaming", f"{day}.json"), board)
        pulled = C.STATE / "stats" / "pulled" / "gaming__facebook_reels.json"
        pulled.parent.mkdir(parents=True, exist_ok=True)
        pulled.write_text(json.dumps({"videos": [{"platform_id": "F5", "url": "https://fb/F5", "text": "By hand\nx",
                                                  "published_at": gt(NOW - timedelta(days=2))},
                                                 {"platform_id": "F6", "published_at": gt(NOW - timedelta(days=40))}]}))
        idx = E.build_posts_index(self.cfg, self.gta, NOW)
        self.assertEqual(idx["M7"]["slot"], "07:00")
        self.assertEqual((idx["M7"]["day"], idx["M7"]["factory"], idx["M7"]["title"]), (str(day), True, "The Chase"))
        self.assertEqual((idx["F5"]["title"], idx["F5"]["factory"]), ("By hand", False))
        self.assertNotIn("F6", idx)


class CliTest(Base):
    def test_1_status_list_and_poll(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            E.cli(self.cfg, ["status"])
        self.assertIn("Gaming: reading comments is off", buf.getvalue())
        with self.assertRaises(SystemExit):
            E.cli(self.cfg, ["poll", "gaming"])
        self.on()
        self.media(("M1", 1, NOW - timedelta(hours=2)))
        self.comments_on("M1", [ig_comment("C1", "source?")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            E.cli(self.cfg, ["poll", "gaming"])
            E.cli(self.cfg, ["list", "gaming"])
        self.assertIn("ig:C1", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            E.cli(self.cfg, ["check", "--quiet"])
        self.assertEqual(buf.getvalue(), "")

    def test_2_kick_check_waits_two_minutes_and_never_starts_in_a_test(self):
        self.assertIn("off for every account", E.kick_check(self.cfg, NOW))
        self.on()
        self.assertIn("Not started", E.kick_check(self.cfg, NOW))
        store.write(store.path("social", "engage-check.json"), {"at": NOW.isoformat()})
        self.assertIn("next check", E.kick_check(self.cfg, NOW + timedelta(seconds=30)))
        with mock.patch("clipfactory.net.why_not", return_value=""), mock.patch("subprocess.Popen") as pop:
            self.assertIn("Checking now", E.kick_check(self.cfg, NOW + timedelta(minutes=3)))
        self.assertEqual(pop.call_args[0][0][-3:], ["engage", "check", "--quiet"])


class DoctorTest(Base):
    def test_1_ok_line_and_messages(self):
        self.on(engage__messages="on")
        self.media()
        self.g.on("GET", rf"/{PAGE}/conversations$", {"data": []})
        self.tick()
        rows = E.doctor(self.cfg, self.clock())
        self.assertIn((True, "Gaming inbox (Instagram): read just now, 0 unanswered", ""), rows)
        self.assertIn((None, "Gaming messages: read; answers reach only app testers until Meta's review "
                             "(Advanced Access)", ""), rows)
        self.assertIn((None, "Gaming replies from the Inbox: off (factory.py social on gaming replies "
                             "--practice)", ""), rows)

    def test_2_never_raises(self):
        self.on()
        (C.STATE / "gaming" / "engage").mkdir(parents=True, exist_ok=True)
        (C.STATE / "gaming" / "engage" / "threads.json").write_text("{broken")
        rows = E.doctor(self.cfg, NOW)
        self.assertTrue(any(r[0] is False and "threads.json will not read" in r[1] for r in rows))
        with mock.patch.object(SS, "cursor", side_effect=RuntimeError("boom")):
            rows = E.doctor(self.cfg, NOW)
        self.assertTrue(any("could not check" in r[1] for r in rows))


class YouTubeQuotaTest(Base):
    PLATFORMS = ["youtube"]

    def test_3_quota_holds_youtube_reads_until_the_pacific_day_turns(self):
        self.on()
        self.yt.on("GET", r"/commentThreads$", google_error("quotaExceeded"))
        self.tick()
        self.assertIn("youtube|read", dict(OW.active_holds(self.cfg, self.gta, NOW)))
        n = len(self.yt.calls)
        self.tick(20)
        self.assertEqual(len(self.yt.calls), n)
        self.assertIn("paused", SS.cursor(self.gta, "youtube")["waits"])



class PrivacyHoldsTest(Base):
    """Review of 2026-10-20 (privacy): a forget, engage.keep_days and a STOP's reason each hold past the next poll
    and the prune."""
    PLATFORMS = ["instagram", "facebook", "youtube"]

    def everything_read_again(self):
        self.g.calls.clear()
        self.yt.calls.clear()

    def test_1_a_forgotten_person_is_never_read_back_in(self):
        self.on(engage__messages="on")
        self.media(("M1", 2, NOW - timedelta(hours=3)))
        self.comments_on("M1", [ig_comment("C1", "source?"), ig_comment("C2", "hi", "fan_two", "222")])
        self.yt.on("GET", r"/commentThreads$", {"items": [
            YouTubeTest.thread(self, "Y1", "credit?", "fan_one", "UC111"),
            YouTubeTest.thread(self, "Y2", "W", "Fan", "UCfan")]})
        self.g.on("GET", rf"/{PAGE}/video_reels$", {"data": []})
        upd = {"t": NOW}
        self.g.on("GET", rf"/{PAGE}/conversations$", lambda call: {"data": [
            {"id": "T1", "updated_time": gt(upd["t"])}]} if call["params"].get("platform") == "instagram" else
            {"data": []})
        self.g.on("GET", r"^/T1$", {"messages": {"data": [
            {"id": "m1", "created_time": gt(NOW - timedelta(minutes=2)), "from": {"id": "111", "username": "fan_one"},
             "to": {"data": [{"id": IG}]}, "message": "where is this from"}]}})
        self.tick()
        self.assertEqual(sorted(i["id"] for i in self.seen), ["ig:C1", "ig:C2", "igm:m1", "yt:Y1", "yt:Y2"])
        for person in ("instagram:111", "youtube:UC111"):
            SS.forget(self.gta, person, "Andrew", cfg=self.cfg)
        raw = (SS.root(self.gta) / "forgotten.json").read_text()
        self.assertNotIn("instagram:", raw)                                  # the key itself is never written
        self.assertNotIn("UC111", raw)
        self.seen.clear()
        self.everything_read_again()
        upd["t"] = NOW + timedelta(minutes=40)                                # the conversation moved: read again
        for _ in range(4):
            self.tick(31)                                                     # the 30-minute reads, YouTube's 15
        self.assertGreater(self.fetched("M1"), 0)
        self.assertTrue(self.yt.calls)
        self.assertTrue([c for c in self.g.calls if c["path"] == "/T1"])
        for iid in ("ig:C1", "yt:Y1", "igm:m1"):
            self.assertIsNone(SS.get_item(self.gta, iid), iid)
        self.assertIsNone(SS.thread(self.gta, "igt:T1"))
        for person in ("instagram:111", "youtube:UC111"):
            self.assertEqual(SS.contact(self.gta, person)["name"], "")
            self.assertIsNone(SS.contact(self.gta, person)["first_at"])
        self.assertEqual(self.seen, [])                                       # the automations never see them again
        self.assertIsNotNone(SS.get_item(self.gta, "ig:C2"))                  # everyone else stays
        self.assertIsNotNone(SS.get_item(self.gta, "yt:Y2"))
        files = "".join(f.read_text() for f in SS.root(self.gta).glob("*.json*"))
        self.assertNotIn("fan_one", files)

    def test_1b_our_reply_naming_a_forgotten_person_goes_and_is_never_read_back_in(self):
        self.on()
        mine = {"id": "R2", "text": "@fan_one it is from MrBossFTW", "timestamp": gt(NOW - timedelta(minutes=10)),
                "username": "gaming", "from": {"id": IG, "username": "gaming"}}
        theirs = {"id": "R3", "text": "same here", "timestamp": gt(NOW - timedelta(minutes=8)),
                  "username": "fan_two", "from": {"id": "222", "username": "fan_two"}}
        self.media(("M1", 3, NOW - timedelta(hours=3)))
        self.comments_on("M1", [ig_comment("C1", "source?", replies=[mine, theirs])])
        self.tick()
        self.assertIsNotNone(SS.get_item(self.gta, "ig:R2"))
        SS.forget(self.gta, "instagram:111", "Andrew", cfg=self.cfg)
        self.assertIsNone(SS.get_item(self.gta, "ig:R2"))
        self.everything_read_again()
        for _ in range(3):
            self.tick(31)
        self.assertGreater(self.fetched("M1"), 0)
        self.assertIsNone(SS.get_item(self.gta, "ig:C1"))
        self.assertIsNone(SS.get_item(self.gta, "ig:R2"))
        self.assertIsNotNone(SS.get_item(self.gta, "ig:R3"))                  # someone else's reply stays
        self.assertNotIn("fan_one", json.dumps(SS.items(self.gta)))

    def test_2_keep_days_below_30_never_reads_a_pruned_comment_back(self):
        self.on(engage__messages="on")
        SW.set_setting(self.cfg, self.gta, "engage.keep_days", 7, "Andrew")
        self.media()
        self.g.on("GET", rf"/{PAGE}/video_reels$", {"data": [{"id": "V1", "created_time": gt(NOW - timedelta(days=10)),
                                                              "comments": {"summary": {"total_count": 2}}}]})
        self.g.on("GET", r"^/V1/comments$", {"data": [
            {"id": "V1_C1", "from": {"id": "777", "name": "Pat Smith"}, "message": "where is this from",
             "created_time": gt(NOW - timedelta(days=9))},
            {"id": "V1_C2", "from": {"id": "778", "name": "Lee"}, "message": "part 2?",
             "created_time": gt(NOW - timedelta(days=2))}]})
        self.yt.on("GET", r"/commentThreads$", {"items": []})
        self.g.on("GET", rf"/{PAGE}/conversations$", lambda call: {"data": [
            {"id": "T9", "updated_time": gt(NOW - timedelta(days=8))}]} if call["params"].get("platform") == "facebook"
            else {"data": []})
        self.g.on("GET", r"^/T9$", {"messages": {"data": [
            {"id": "m9", "created_time": gt(NOW - timedelta(days=8)), "from": {"id": "779", "name": "Old DM"},
             "to": {"data": [{"id": PAGE}]}, "message": "hello?"}]}})
        for _ in range(30):                                   # a day and more of polls: the prune runs, reels re-read
            self.tick(60)
        self.assertGreater(sum(1 for c in self.g.calls if c["path"] == "/V1/comments"), 1)
        self.assertIsNone(SS.get_item(self.gta, "fb:V1_C1"))
        self.assertIsNone(SS.contact(self.gta, "facebook:777")["first_at"])
        self.assertIsNone(SS.thread(self.gta, "fbt:T9"))
        self.assertIsNone(SS.get_item(self.gta, "fbm:m9"))
        self.assertEqual([i["id"] for i in self.seen], ["fb:V1_C2"])         # the one inside 7 days, once
        self.assertEqual(SS.get_item(self.gta, "fb:V1_C2")["state"], "new")

    def test_3_a_stop_keeps_no_words(self):
        self.on()
        self.media(("M1", 1, NOW - timedelta(hours=3)))
        self.comments_on("M1", [ig_comment("C1", "STOP replying, I live at 12 Elm St and I am 14", "kid", "444")],
                         times=1)
        self.tick()
        c = SS.contact(self.gta, "instagram:444")
        self.assertEqual((c["stop"], c["stop_why"]), (True, "wrote STOP in a comment (ig:C1)"))
        SS.stop_contact(self.gta, "instagram:555", NOW, why="wrote: STOP, my number is 555 0101")   # an older STOP
        SS.prune(self.gta, NOW + timedelta(days=200))
        self.assertIsNone(SS.get_item(self.gta, "ig:C1"))
        for key in ("instagram:444", "instagram:555"):
            c = SS.contact(self.gta, key)
            self.assertTrue(c["stop"], key)                                   # STOP is forever
            self.assertEqual(c["name"], "")                                   # the name goes after 90 days
            self.assertTrue(c["stop_why"].startswith("wrote STOP"), c["stop_why"])
        raw = (SS.root(self.gta) / "contacts.json").read_text()
        for word in ("Elm St", "kid", "0101"):
            self.assertNotIn(word, raw)


if __name__ == "__main__":
    unittest.main()
