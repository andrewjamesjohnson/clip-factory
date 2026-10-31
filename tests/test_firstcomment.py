import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The first comment under every post (firstcomment.py, 2026-10-20): defaults from the clip's real source (GTA credits
# the creator with the link at the moment; the Politics cites the event, neutral), nothing written while off, the
# poster's hook queues it a little after the post went live, only on posts the factory made, YouTube only once public
# and with its scope, the marker goes "sending" before the call, a lost answer is found, practice never calls, the
# Facebook post-id fallback, the card's Save is a page click on a clip waiting for Approve, the doctor and the CLI.
# Fake platforms and throwaway folders only; nothing can reach the network.

import contextlib
import io
import json
import random
import shutil
import socket
import subprocess
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import (Clock, FakeGraph, FakeYouTube, cfg_in_tmp, meta_error,  # noqa: E402
                          patch_http, serve, slot_posted, slot_ready, write_tokens)

from clipfactory import board as B              # noqa: E402
from clipfactory import capabilities as CAP     # noqa: E402
from clipfactory import answers as AN           # noqa: E402
from clipfactory import extras as EX            # noqa: E402
from clipfactory import rules as RU             # noqa: E402
from clipfactory import firstcomment as FC      # noqa: E402
from clipfactory import graph as G             # noqa: E402
from clipfactory import outward as OW           # noqa: E402
from clipfactory import registry as REG         # noqa: E402
from clipfactory import review as R             # noqa: E402
from clipfactory import signoff as S            # noqa: E402
from clipfactory import social as SOC           # noqa: E402
from clipfactory import store                   # noqa: E402
from clipfactory import switches as SW          # noqa: E402

DAY = date(2031, 7, 8)
K = "10:30"
SRC = {"host": "MrBossFTW", "url": "https://www.youtube.com/watch?v=uSQfYvi-NfY", "t": 190.4, "many": False,
       "title": "", "date": "", "kind": "video"}
GTA_FB = "Full video from MrBossFTW: https://www.youtube.com/watch?v=uSQfYvi-NfY&t=190s"
GTA_IG = "From MrBossFTW's stream on YouTube."
META = ["pages_manage_engagement", "pages_read_engagement", "pages_read_user_content", "instagram_basic",
        "instagram_manage_comments", "instagram_content_publish", "pages_manage_posts"]


def said(fn, *a, **kw) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        self.clock = Clock(self.cfg.slot_time(DAY, "10:20")).patch(self, self.cfg)
        FC.RNG.seed(7)
        OW.RNG.seed(7)
        self.addCleanup(SW._SEEN.clear)

    def modes(self, acct=None, **modes) -> None:
        acct = acct or self.gta
        p = SW.file_path(acct)
        raw = store.read(p, {}) or {}
        raw.setdefault("modes", {}).update({k.replace("__", "."): v for k, v in modes.items()})
        store.write(p, raw)
        SW._SEEN.clear()

    def scopes(self, acct=None, meta=META, youtube=("youtube.upload", "youtube.readonly")) -> None:
        acct = acct or self.gta
        CAP.save_scopes(acct, "meta", list(meta), "connect")
        CAP.save_scopes(acct, "youtube", list(youtube), "connect")

    def slot(self, k=K, day=DAY, acct=None) -> dict:
        return B.read(acct or self.gta, day)["slots"][k]

    def with_words(self, k=K, fb=GTA_FB, ig=GTA_IG, yt=GTA_FB, day=DAY) -> dict:
        """A clip waiting for Approve with its first comments, then signed and posted by the factory."""
        slot_ready(self, self.gta, day, k)
        with EX.edit(self.cfg, self.gta, day, k, "Andrew", "first comment") as x:
            fc = {}
            for o, t in (("facebook_reels", fb), ("instagram_reels", ig), ("youtube_shorts", yt)):
                if t:
                    fc[o] = {"text": t, "from": "default"}
            x["first_comment"] = fc
        return slot_posted(self, self.gta, day, k)[k]

    def hook(self, outlet="facebook_reels", k=K, day=DAY):
        s = self.slot(k, day)
        return FC.on_posted(self.cfg, self.gta, day, s, outlet, s["outlets"][outlet], self.clock())

    def items(self, acct=None):
        return list(OW._items((acct or self.gta).slug).values())

    def marker(self, outlet="facebook_reels", k=K, day=DAY) -> dict:
        return FC.marker(self.slot(k, day)["outlets"][outlet])


# ------------------------------------------------------------------------------------------------------ defaults
class DefaultsTest(Base):
    def test_gta_credits_the_creator_with_the_moment_and_instagram_has_no_link(self):
        s = {"host": "MrBossFTW", "source_url": "https://www.youtube.com/watch?v=uSQfYvi-NfY",
             "pick_span": [190.4, 228], "format": "underlay", "copy": {}}
        got = FC.defaults(self.gta, s, FC.OUTLETS, DAY)
        self.assertEqual(got, {"facebook_reels": GTA_FB, "instagram_reels": GTA_IG, "youtube_shorts": GTA_FB})
        self.assertNotIn("http", got["instagram_reels"])
        twitch = dict(s, source_url="https://www.twitch.tv/videos/123", host="Kyle")
        self.assertEqual(FC.defaults(self.gta, twitch, FC.OUTLETS, DAY)["instagram_reels"],
                         "From Kyle's stream on Twitch.")
        many = dict(s, format="reactions", host="iShowSpeed, Kai Cenat",
                    source_urls=["https://youtu.be/a", "https://youtu.be/b"])
        self.assertEqual(set(FC.defaults(self.gta, many, FC.OUTLETS, DAY).values()),
                         {"Reactions from iShowSpeed, Kai Cenat."})

    def test_no_source_no_default(self):
        self.assertEqual(FC.defaults(self.gta, {"copy": {}, "format": "underlay"}, FC.OUTLETS, DAY), {})
        self.assertEqual(FC.defaults(self.politics, {"copy": {}, "slug": "x", "host": "C-SPAN"}, FC.OUTLETS, DAY), {})
        only_host = {"copy": {}, "host": "MrBossFTW"}            # Instagram can credit him; no link for the others
        self.assertEqual(FC.defaults(self.gta, only_host, FC.OUTLETS, DAY),
                         {"instagram_reels": "From MrBossFTW's stream."})

    def politics_job(self, title="Secy. of State Rubio defends Trump suspending Russian sanctions", fmt="briefing",
                   when="OCTOBER 19 2026", url="https://www.youtube.com/watch?v=hWqCDkQhi3c") -> dict:
        jd = self.politics.account_dir / "Jobs" / "CSPAN Rubio Evidence 10.19.26"
        jd.mkdir(parents=True, exist_ok=True)
        (jd / "project.json").write_text(json.dumps({"source_url": url, "source_title": title,
                                                     "source_channel": "C-SPAN", "source_date": when,
                                                     "format": fmt}))
        (jd / "slate.json").write_text(json.dumps({"clips": [{"slug": "rubio-sanctions", "format": fmt}]}))
        return {"slug": "rubio-sanctions", "host": "C-SPAN", "copy": {},
                "cast_check": {"job": "CSPAN Rubio Evidence 10.19.26"}}

    def test_politics_cites_the_event_neutrally(self):
        s = self.politics_job()
        got = FC.defaults(self.politics, s, FC.OUTLETS, DAY)
        title = "Secy. of State Rubio defends Trump suspending Russian sanctions, C-SPAN, October 19, 2026."
        self.assertEqual(got["facebook_reels"], title + " Full briefing: https://www.youtube.com/watch?v=hWqCDkQhi3c")
        self.assertEqual(got["youtube_shorts"], got["facebook_reels"])
        self.assertEqual(got["instagram_reels"], title)
        hearing = self.politics_job(title="Nomination Hearing, February 27 2025 \u2014 Senate Banking", fmt="hearing",
                                  when="", url="https://www.senate.gov/isvp/?comm=banking")
        got = FC.defaults(self.politics, hearing, FC.OUTLETS, DAY)
        self.assertEqual(got["facebook_reels"], "Nomination Hearing, February 27 2025, Senate Banking, C-SPAN. Full "
                                                "hearing: https://www.senate.gov/isvp/?comm=banking")
        for t in got.values():
            self.assertFalse(any(d in t for d in FC.DASHES))
            self.assertNotIn("#", t)

    def test_engagement_bait_is_refused_on_every_platform(self):
        # 2026-10-20 review: the narrow ASKS let the canonical bait through; the first comment now reads the same
        # list the automations do (answers.BAIT)
        for bad in ("Comment GTA to get the full stream link", "Type SOURCE below and we will send you the link",
                    "Follow for part 2", "Subscribe for more GTA 6 news", "Share this with a friend who plays GTA",
                    "DM us for the full video", "Link in bio for the full stream",
                    "Full video: https://youtu.be/abc Comment LINK to get part 2"):
            self.assertTrue(RU.bait(bad), bad)
            for o in FC.OUTLETS:
                with self.assertRaises(SystemExit, msg=f"{o}: {bad}") as e:
                    FC.check_text(o, bad, qs=[])
                self.assertIn("engagement bait", str(e.exception))
        self.assertIs(RU.BAIT, AN.BAIT)                                   # one list for both
        slot_ready(self, self.gta, DAY, K)                                # the page and the extras CLI's one gate
        with self.assertRaises(SystemExit):
            FC.set_comment(self.cfg, self.gta, DAY, K, "facebook_reels", "Comment GTA to get the full stream link",
                           "task", how="task")
        self.assertEqual(FC.set_comment(self.cfg, self.gta, DAY, K, "facebook_reels", GTA_FB, "task", how="task"),
                         GTA_FB)
        self.assertEqual(FC.check_text("facebook_reels", GTA_FB, qs=[]), GTA_FB)
        self.assertEqual(FC.check_text("instagram_reels", GTA_IG, qs=[]), GTA_IG)

    def test_a_credited_source_name_is_no_bait(self):
        title = "Hearing on Follow-Up to the Border Audit"
        self.assertTrue(RU.bait(title))                                   # the shared list alone would refuse it
        s = self.politics_job(title=title, fmt="hearing")
        got = FC.defaults(self.politics, s, FC.OUTLETS, DAY)
        self.assertEqual(got["instagram_reels"], title + ", C-SPAN, October 19, 2026.")
        self.assertEqual(got["facebook_reels"], title + ", C-SPAN, October 19, 2026. Full hearing: "
                                                        "https://www.youtube.com/watch?v=hWqCDkQhi3c")
        self.assertEqual(set(got), set(FC.OUTLETS))
        for o, t in got.items():
            self.assertEqual(FC.check_text(o, t, credit=FC.credits(FC.source(self.politics, s, DAY))), t)
        with self.assertRaises(SystemExit):                               # the credit covers its own words only
            FC.check_text("facebook_reels", title + ". Follow for part 2.",
                          credit=FC.credits(FC.source(self.politics, s, DAY)))
        gta = dict(SRC, host="TagTeamTV")
        self.assertEqual(FC.check_text("instagram_reels", "From TagTeamTV's stream on YouTube.",
                                       credit=FC.credits(gta)), "From TagTeamTV's stream on YouTube.")

    def test_politics_never_repeats_the_captions_question(self):
        s = self.politics_job(title="Will the shutdown end this week?")
        cap = self.tmp / "reels.txt"
        cap.write_text("TITLE\nRubio on sanctions\nCAPTION\nRubio answers. Will the shutdown end this week?\n")
        s["copy"] = {"instagram_reels": {"path": str(cap)}}
        got = FC.defaults(self.politics, s, FC.OUTLETS, DAY)
        self.assertNotIn("instagram_reels", got)                  # no title left to cite on Instagram
        self.assertEqual(got["facebook_reels"], "C-SPAN, October 19, 2026. Full briefing: "
                                                "https://www.youtube.com/watch?v=hWqCDkQhi3c")

    def test_the_words_are_checked(self):
        for bad, why in (("", "empty"), ("Full video \u2014 here", "dash"), ("Source #gta6", "hashtag"),
                         ("Drop a comment below", "asks"), ("Tag a friend who needs this", "asks"),
                         ("x" * 2201, "2,200")):
            with self.assertRaises(SystemExit, msg=why) as e:
                FC.check_text("instagram_reels", bad)
            self.assertTrue(str(e.exception))
        self.assertEqual(FC.check_text("youtube_shorts", "y" * 3000), "y" * 3000)
        self.assertEqual(FC.check_text("facebook_reels", "  " + GTA_FB + "\n"), GTA_FB)  # a link's #t is no hashtag
        self.assertEqual(FC.check_text("facebook_reels", "https://example.com/a#part2"), "https://example.com/a#part2")
        with self.assertRaises(SystemExit) as e:
            FC.check_text("facebook_reels", "Is this a big deal for GTA 6? Watch the stream.",
                          qs=["is this a big deal for gta 6"])
        self.assertIn("caption's question", str(e.exception))


# ------------------------------------------------------------------------------------------------------ collect
class CollectTest(Base):
    def test_nothing_is_written_while_off(self):
        with mock.patch.object(FC, "source", return_value=dict(SRC)):
            s = slot_ready(self, self.gta, DAY, K)[K]
        self.assertNotIn("extras", s)
        self.assertFalse((self.cfg_path.parent / "state" / "gaming" / "extras").exists())

    def test_collect_writes_the_default_for_each_switch_not_off(self):
        self.modes(first_comment__facebook_reels="practice", first_comment__instagram_reels="on")
        with mock.patch.object(FC, "source", return_value=dict(SRC)):
            s = slot_ready(self, self.gta, DAY, K)[K]
        data = EX.read(s)
        self.assertEqual(data["first_comment"], {"facebook_reels": {"text": GTA_FB, "from": "default"},
                                                 "instagram_reels": {"text": GTA_IG, "from": "default"}})
        self.assertEqual(data["edited"][0]["by"], "collect")
        S.approve(self.cfg, self.gta, DAY, [K], "Andrew")             # signed with the clip
        s = self.slot()
        self.assertEqual(s["signoff"]["hash"], B.content_hash(s))
        self.assertIn("extras", s)
        self.assertEqual(B.verify(s), [])

    def test_no_source_writes_nothing(self):
        self.modes(first_comment__facebook_reels="on")
        with mock.patch.object(FC, "source", return_value={}):
            s = slot_ready(self, self.gta, DAY, K)[K]
        self.assertNotIn("extras", s)


# ------------------------------------------------------------------------------------------------------ the hook
class HookTest(Base):
    def setUp(self):
        super().setUp()
        write_tokens(self.gta)
        self.scopes()

    def test_the_hook_queues_with_the_delay_from_published_at(self):
        self.modes(first_comment__facebook_reels="on")
        s = self.with_words()
        self.clock.at("10:31")
        rng = random.Random(7)
        delay = rng.uniform(90, 300)
        got = self.hook()
        self.assertIn("first comment queued", got)
        (it,) = self.items()
        live = B.read(self.gta, DAY)["slots"][K]["outlets"]["facebook_reels"]["published_at"]
        lt = FC._when(live)
        self.assertEqual(it["not_before"], FC._iso(lt + timedelta(seconds=delay)))
        self.assertEqual(it["expires"], FC._iso(lt + timedelta(hours=20)))
        self.assertEqual(it["key"], f"fc:facebook:{s['outlets']['facebook_reels']['post_id']}")
        self.assertEqual(it["signed"]["what"], "clip")
        self.assertEqual(it["signed"]["hash"], s["signoff"]["hash"])
        self.assertEqual(it["signed"]["extras_sha"], s["extras"]["sha256"])
        self.assertEqual(it["text"], GTA_FB)
        self.assertEqual(self.marker()["state"], "queued")
        self.assertEqual(self.marker()["action"], it["id"])
        self.assertIsNone(self.hook())                                   # never queued twice
        self.assertEqual(len(self.items()), 1)

    def test_off_queues_nothing_and_writes_nothing(self):
        self.with_words()
        self.assertIsNone(self.hook())
        self.assertEqual(self.items(), [])
        self.assertEqual(self.marker(), {})
        self.assertFalse((self.tmp / "state" / "gaming" / "outward").exists())

    def test_matched_records_are_never_commented(self):
        self.modes(first_comment__facebook_reels="on", first_comment__youtube_shorts="on")
        self.with_words()
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"][K]["outlets"]["facebook_reels"]["provider"] = "matched"
            bd["slots"][K]["outlets"]["youtube_shorts"].pop("clip_sha", None)
        self.assertIsNone(self.hook())
        self.assertIsNone(self.hook("youtube_shorts"))
        self.assertEqual(FC.sweep(self.cfg, now=self.clock()), [])
        self.assertEqual(self.items(), [])

    def test_a_clip_changed_after_signing_or_too_old_is_not_queued(self):
        self.modes(first_comment__facebook_reels="on")
        self.with_words()
        self.clock.advance(hours=21)
        self.assertIsNone(self.hook())
        self.clock.advance(hours=-20)
        p = Path(self.slot()["extras"]["path"])
        p.write_text(p.read_text().replace("MrBossFTW", "Someone"))
        self.assertIsNone(self.hook())
        self.assertEqual(self.items(), [])

    def test_sweep_catches_a_miss(self):
        self.modes(first_comment__facebook_reels="practice")
        self.with_words()
        self.clock.at("10:40")
        lines = FC.sweep(self.cfg, now=self.clock())
        self.assertEqual(len(lines), 1)
        self.assertIn("first comment practice", lines[0])
        self.assertEqual(self.marker()["mode"], "practice")
        self.assertEqual(FC.sweep(self.cfg, now=self.clock()), [])

    def test_practice_makes_no_call(self):
        self.modes(first_comment__facebook_reels="practice")
        g = FakeGraph()                                       # no routes: any call fails the test
        patch_http(self, graph=g, yt=FakeYouTube())
        self.with_words()
        self.hook()
        self.clock.at("10:40")
        (it,) = self.items()
        res = OW.send(self.cfg, self.gta, it["id"], self.clock())
        self.assertEqual(res["state"], "practice")
        self.assertEqual(g.calls, [])
        m = self.marker()
        self.assertEqual(m["state"], "practice")
        self.assertIn("would have posted", FC.state_line(self.cfg, self.gta, self.slot(), "facebook_reels")[1])
        rows = list(OW.rows(self.cfg, self.gta, kinds=("first_comment",), states=("practice",)))
        self.assertEqual([r["text"] for r in rows], [GTA_FB])

    def test_the_marker_goes_sending_before_the_call_then_done(self):
        self.modes(first_comment__instagram_reels="on")
        seen = []
        g = FakeGraph().on("POST", r"/\d+/comments$",
                           lambda call: seen.append(self.marker("instagram_reels").get("state")) or {"id": "c-1"})
        patch_http(self, graph=g)
        self.with_words()
        self.hook("instagram_reels")
        self.clock.at("10:40")
        (it,) = self.items()
        res = OW.send(self.cfg, self.gta, it["id"], self.clock())
        self.assertEqual(res["state"], "done")
        self.assertEqual(seen, ["sending"])
        (call,) = g.calls
        self.assertEqual(call["form"]["message"], GTA_IG)
        self.assertEqual(call["path"], f"/{self.slot()['outlets']['instagram_reels']['post_id']}/comments")
        m = self.marker("instagram_reels")
        self.assertEqual((m["state"], m["id"]), ("done", "c-1"))
        self.assertEqual(OW.key_state(self.cfg, self.gta, it["key"]), "done")
        self.assertEqual(FC.sweep(self.cfg, now=self.clock()), [])          # never twice

    def test_the_facebook_post_id_fallback(self):
        self.modes(first_comment__facebook_reels="on")
        s = self.with_words()
        vid = s["outlets"]["facebook_reels"]["post_id"]
        unsupported = meta_error(100, 33, msg="(#100) Unsupported post request. Object with ID does not support "
                                              "this operation")
        g = (FakeGraph()
             .on("POST", rf"/{vid}/comments$", unsupported)
             .on("GET", rf"/{vid}$", {"post_id": "1000001_777", "id": vid})
             .on("POST", r"/1000001_777/comments$", {"id": "1000001_777_9"}))
        patch_http(self, graph=g)
        self.hook()
        self.clock.at("10:40")
        res = OW.send(self.cfg, self.gta, self.items()[0]["id"], self.clock())
        self.assertEqual(res["state"], "done")
        self.assertEqual(g.paths(), [f"POST /{vid}/comments", f"GET /{vid}", "POST /1000001_777/comments"])
        self.assertEqual(g.calls[1]["params"]["fields"], "post_id")
        self.assertEqual(self.marker()["id"], "1000001_777_9")

    def test_a_lost_answer_is_found_not_sent_again(self):
        self.modes(first_comment__facebook_reels="on")
        s = self.with_words()
        vid = s["outlets"]["facebook_reels"]["post_id"]
        g = (FakeGraph().on("POST", rf"/{vid}/comments$", socket.timeout("timed out"))
             .on("GET", rf"/{vid}/comments$", {"data": [
                 {"id": "x1", "message": GTA_FB, "from": {"id": "999", "name": "Someone else"}},
                 {"id": "c-ours", "message": GTA_FB, "from": {"id": "1000001", "name": "Gaming"}}]}))
        patch_http(self, graph=g)
        self.hook()
        self.clock.at("10:40")
        aid = self.items()[0]["id"]
        res = OW.send(self.cfg, self.gta, aid, self.clock())
        self.assertEqual(res["state"], "sending")
        self.assertEqual(self.marker()["state"], "sending")
        self.clock.advance(minutes=11)
        res = OW.send(self.cfg, self.gta, aid, self.clock())
        self.assertEqual(res["state"], "done")
        self.assertEqual(g.paths(), [f"POST /{vid}/comments", f"GET /{vid}/comments"])
        self.assertEqual(self.marker()["id"], "c-ours")

    def test_an_edit_or_a_pull_after_queueing_skips_it(self):
        self.modes(first_comment__facebook_reels="on")
        g = FakeGraph()
        patch_http(self, graph=g)
        self.with_words()
        self.hook()
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"][K]["status"] = B.READY                     # pulled
        self.clock.at("10:40")
        res = OW.send(self.cfg, self.gta, self.items()[0]["id"], self.clock())
        self.assertEqual(res["state"], "skipped")
        self.assertEqual(g.calls, [])
        m = self.marker()
        self.assertEqual(m["state"], "skipped")
        self.assertIn("pulled", m["why"])


class YouTubeTest(Base):
    def setUp(self):
        super().setUp()
        write_tokens(self.gta)

    def test_youtube_waits_for_its_scope_then_for_public(self):
        self.modes(first_comment__youtube_shorts="on")
        self.scopes()                                                     # no force-ssl yet
        s = self.with_words()
        vid = s["outlets"]["youtube_shorts"]["video_id"]
        self.clock.at("10:31")
        self.assertIn("waits for youtube.force-ssl", self.hook("youtube_shorts"))
        self.assertEqual(self.items(), [])
        self.assertEqual(self.marker("youtube_shorts")["state"], "waits")
        self.assertIn("needs youtube.force-ssl",
                      FC.state_line(self.cfg, self.gta, self.slot(), "youtube_shorts")[1])
        self.assertEqual(FC.sweep(self.cfg, now=self.clock()), [])        # still missing: left waiting
        self.scopes(youtube=("youtube.upload", "youtube.readonly", "youtube.force-ssl"))
        (line,) = FC.sweep(self.cfg, now=self.clock())
        self.assertIn("first comment queued", line)
        (it,) = self.items()
        live = FC._when(self.slot()["outlets"]["youtube_shorts"]["at"])
        self.assertGreaterEqual(FC._when(it["not_before"]), live + timedelta(seconds=600))
        self.assertLessEqual(FC._when(it["not_before"]), live + timedelta(seconds=1200))
        status = {"privacyStatus": "private"}
        yt = (FakeYouTube().on("GET", r"/videos$", lambda call: {"items": [{"id": vid, "status": dict(status)}]})
              .on("POST", r"/commentThreads$", {"id": "Ugz-thread-1"}))
        patch_http(self, graph=FakeGraph(), yt=yt)
        self.clock.set(FC._when(it["not_before"]) + timedelta(seconds=1))
        res = OW.send(self.cfg, self.gta, it["id"], self.clock())
        self.assertEqual(res["state"], "skipped")                         # not public yet: it looks again later
        self.assertEqual(yt.paths(), ["GET /videos"])
        m = self.marker("youtube_shorts")
        self.assertEqual((m["state"], m["looks"]), ("waits", 1))
        self.assertEqual(FC._when(m["look_at"]), self.clock() + timedelta(minutes=20))
        self.assertIn("public yet", FC.state_line(self.cfg, self.gta, self.slot(), "youtube_shorts")[1])
        self.clock.advance(minutes=10)
        self.assertEqual(FC.sweep(self.cfg, now=self.clock()), [])        # not time to look again yet
        status["privacyStatus"] = "public"
        self.clock.advance(minutes=11)
        (line,) = FC.sweep(self.cfg, now=self.clock())
        self.assertIn("first comment queued", line)
        (it,) = self.items()
        res = OW.send(self.cfg, self.gta, it["id"], self.clock())
        self.assertEqual(res["state"], "done")
        self.assertEqual(yt.paths(), ["GET /videos", "GET /videos", "POST /commentThreads"])
        body = yt.calls[-1]["body"]
        self.assertEqual(body, {"snippet": {"videoId": vid, "topLevelComment": {"snippet": {"textOriginal": GTA_FB}}}})
        m = self.marker("youtube_shorts")
        self.assertEqual(m["state"], "done")
        self.assertIn("lc=Ugz-thread-1", m["url"])

    def test_youtube_never_public_is_skipped_after_two_hours(self):
        self.modes(first_comment__youtube_shorts="on")
        self.scopes(youtube=("youtube.upload", "youtube.force-ssl"))
        s = self.with_words()
        vid = s["outlets"]["youtube_shorts"]["video_id"]
        self.hook("youtube_shorts")
        yt = FakeYouTube().on("GET", r"/videos$", {"items": [{"id": vid, "status": {"privacyStatus": "private"}}]})
        patch_http(self, graph=FakeGraph(), yt=yt)
        self.clock.advance(hours=3)
        res = OW.send(self.cfg, self.gta, self.items()[0]["id"], self.clock())
        self.assertEqual(res["state"], "skipped")
        self.assertIn("2 hours", self.marker("youtube_shorts")["why"])

    def test_a_lost_youtube_answer_is_found_by_channel_and_words(self):
        self.modes(first_comment__youtube_shorts="on")
        self.scopes(youtube=("youtube.upload", "youtube.force-ssl"))
        s = self.with_words()
        vid = s["outlets"]["youtube_shorts"]["video_id"]
        self.hook("youtube_shorts")
        mine = {"id": "Ugz-ours", "snippet": {"topLevelComment": {"snippet": {
            "authorChannelId": {"value": "UCfakechannel0001"}, "textOriginal": GTA_FB}}}}
        theirs = {"id": "Ugz-theirs", "snippet": {"topLevelComment": {"snippet": {
            "authorChannelId": {"value": "UCsomeoneelse"}, "textOriginal": GTA_FB}}}}
        yt = (FakeYouTube().on("GET", r"/videos$", {"items": [{"id": vid, "status": {"privacyStatus": "public"}}]})
              .on("GET", r"/commentThreads$", {"items": [theirs, mine]}))
        patch_http(self, graph=FakeGraph(), yt=yt)
        it = self.items()[0]
        self.clock.set(FC._when(it["not_before"]) + timedelta(seconds=1))
        with mock.patch.object(G, "yt_write", side_effect=socket.timeout("no answer")):
            res = OW.send(self.cfg, self.gta, it["id"], self.clock())
        self.assertEqual(res["state"], "sending")
        self.clock.advance(minutes=11)
        res = OW.send(self.cfg, self.gta, it["id"], self.clock())
        self.assertEqual(res["state"], "done")
        self.assertEqual(self.marker("youtube_shorts")["id"], "Ugz-ours")
        params = yt.calls[-1]["params"]
        self.assertEqual(params["videoId"], vid)
        self.assertNotIn("searchTerms", params)                   # the newest threads, not a search for a link
        self.assertEqual((params["order"], params["maxResults"]), ("time", "50"))


# ------------------------------------------------------------------------------------------------------ the card
class CardTest(Base):
    def test_the_card_part_draws_nothing_while_off(self):
        s = slot_ready(self, self.gta, DAY, K)[K]
        self.assertEqual(FC.card_part(self.cfg, self.gta, DAY, s, self.clock()), "")

    def test_the_card_part_ready_then_signed(self):
        self.scopes(meta=["pages_manage_engagement"])
        self.modes(first_comment__facebook_reels="practice", first_comment__instagram_reels="on")
        with mock.patch.object(FC, "source", return_value=dict(SRC)):
            s = slot_ready(self, self.gta, DAY, K)[K]
        html = FC.card_part(self.cfg, self.gta, DAY, s, self.clock())
        self.assertIn('<details class="fc" data-k="fc" open><summary>First comment</summary>', html)
        self.assertIn('name="text"', html)
        self.assertIn('data-op="extra-set" data-x=""', html)
        self.assertIn('data-op="extra-clear"', html)
        self.assertIn('<input type="hidden" name="outlet" value="facebook_reels">', html)
        self.assertIn("Facebook post of Gaming 10:30", html)               # the confirm names where it goes
        self.assertIn("needs instagram_manage_comments", html)
        self.assertIn(GTA_FB.replace("&", "&amp;"), html)
        self.assertNotIn("youtube_shorts", html)                              # off and no words: no box
        page = R.page(self.cfg, DAY, self.clock(), tok="t0k", acct="gaming")
        self.assertIn('class="fc"', page)
        self.assertIn(".fc-t{", page)
        S.approve(self.cfg, self.gta, DAY, [K], "Andrew")
        html = FC.card_part(self.cfg, self.gta, DAY, self.slot(), self.clock())
        self.assertNotIn(" open>", html)
        self.assertNotIn("<textarea", html)
        self.assertIn("practice: it waits for the post", html)
        self.assertIn("waits: needs instagram_manage_comments", html)

    def test_the_edit_op_is_ready_only_and_needs_the_page(self):
        self.modes(first_comment__facebook_reels="on")
        slot_ready(self, self.gta, DAY, K)
        get, post = serve(self, self.cfg)
        body = {"op": "extra-set", "account": "gaming", "date": DAY.isoformat(), "slots": [K],
                "fields": {"outlet": "facebook_reels", "text": "Full video: https://youtu.be/abc", "part":
                           "first_comment"}}
        code, got = post(body, page=False)
        self.assertEqual(code, 400)
        self.assertIn("Only a click on the review page", got["message"])
        self.assertNotIn("extras", self.slot())
        code, got = post(body)
        self.assertEqual(code, 200, got)
        self.assertTrue(got["ok"])
        self.assertIn('"Full video: https://youtu.be/abc"', got["message"])
        self.assertEqual(EX.first_comment(self.slot(), "facebook_reels"), "Full video: https://youtu.be/abc")
        self.assertEqual(EX.read(self.slot())["first_comment"]["facebook_reels"]["from"], "edited")
        code, got = post(dict(body, fields={"outlet": "facebook_reels", "text": "Watch \u2014 now"}))
        self.assertEqual(code, 400)
        self.assertIn("long dashes", got["message"])
        code, got = post(dict(body, seen="stale0000000000"))
        self.assertEqual(code, 400)
        self.assertIn("changed since the page showed it", got["message"])
        code, got = post(dict(body, op="extra-clear", fields={"outlet": "facebook_reels", "part": "first_comment"}))
        self.assertEqual(code, 200)
        self.assertNotIn("extras", self.slot())
        post(body)
        S.approve(self.cfg, self.gta, DAY, [K], "Andrew")
        code, got = post(body)
        self.assertEqual(code, 400)
        self.assertIn("It is signed: Pull it to change its first comment.", got["message"])
        self.assertEqual(self.slot()["signoff"]["hash"], B.content_hash(self.slot()))


# ------------------------------------------------------------------------------------------------------ doctor, CLI
class DoctorCliTest(Base):
    def test_the_doctor_lines(self):
        rows = FC.doctor(self.cfg, self.clock())
        self.assertTrue(rows)
        self.assertTrue(all(r[0] is None for r in rows))
        self.assertIn((None, "Gaming first comments on YouTube: off (opt in: connect youtube --with comments; that "
                             "also grants delete power, which the factory never uses)", ""), rows)
        self.scopes(meta=["pages_manage_engagement"])
        self.modes(first_comment__facebook_reels="on", first_comment__instagram_reels="practice")
        rows = FC.doctor(self.cfg, self.clock())
        self.assertIn((True, "Gaming first comments on Facebook: on, 0 posted in 7 days", ""), rows)
        self.assertIn((None, "Gaming first comments on Instagram: practice (waits for instagram_manage_comments)",
                       ""), rows)
        self.scopes(meta=["instagram_manage_comments"])
        rows = FC.doctor(self.cfg, self.clock())
        bad = [r for r in rows if r[0] is False]
        self.assertEqual(len(bad), 1)
        self.assertIn("Facebook: on, but it waits for pages_manage_engagement", bad[0][1])

    def test_the_doctor_counts_what_went(self):
        write_tokens(self.gta)
        self.scopes()
        self.modes(first_comment__facebook_reels="on")
        g = FakeGraph().on("POST", r"/comments$", {"id": "c1"})
        patch_http(self, graph=g)
        self.with_words()
        self.hook()
        self.clock.at("10:40")
        OW.send(self.cfg, self.gta, self.items()[0]["id"], self.clock())
        rows = FC.doctor(self.cfg, self.clock())
        self.assertIn((True, "Gaming first comments on Facebook: on, 1 posted in 7 days", ""), rows)

    def test_the_extras_command(self):
        self.modes(first_comment__facebook_reels="on", poll="practice")
        slot_ready(self, self.gta, DAY, K)
        f = self.tmp / "words.txt"
        f.write_text("Full video from MrBossFTW: https://youtu.be/abc\n")
        out = said(FC.cli, self.cfg, ["comment", "gaming", "--date", DAY.isoformat(), "--slot", K, "--platform",
                                      "facebook", "--text-file", str(f)])
        self.assertIn("the first comment on Facebook is now: Full video from MrBossFTW", out)
        self.assertEqual(EX.read(self.slot())["first_comment"]["facebook_reels"]["from"], "task")
        out = said(FC.cli, self.cfg, ["poll", "gaming", "--date", DAY.isoformat(), "--slot", K,
                                      "--options", "Yes|No"])
        self.assertIn("the Instagram poll is Yes / No", out)
        out = said(FC.cli, self.cfg, ["status", "gaming", "--date", DAY.isoformat()])
        self.assertIn("Facebook (task): Full video from MrBossFTW", out)
        self.assertIn("Instagram poll: Yes / No", out)
        with self.assertRaises(SystemExit):
            FC.cli(self.cfg, ["poll", "gaming", "--date", DAY.isoformat(), "--slot", K, "--options", "Yes"])
        out = said(FC.cli, self.cfg, ["clear", "gaming", "--date", DAY.isoformat(), "--slot", K, "--what", "all"])
        self.assertIn("no first comment, poll or trial now", out)
        self.assertNotIn("extras", self.slot())
        said(FC.cli, self.cfg, ["comment", "gaming", "--date", DAY.isoformat(), "--slot", K, "--platform",
                                "facebook", "--text", "Full video: https://youtu.be/abc"])
        S.approve(self.cfg, self.gta, DAY, [K], "Andrew")
        with self.assertRaises(SystemExit) as e:
            FC.cli(self.cfg, ["comment", "gaming", "--date", DAY.isoformat(), "--slot", K, "--platform",
                              "facebook", "--text", "Something else"])
        self.assertIn("It is signed", str(e.exception))

    def test_the_command_is_on_the_plug_board(self):
        REG.reset()
        self.assertIs(REG.load("firstcomment"), FC)
        self.assertIs(REG.act_module("extra-set"), FC)
        out = said(REG.run_cli, self.cfg, "extras", ["status", "gaming"])
        self.assertIn("Gaming: first comments Facebook off", out)
        self.assertIn("firstcomment", SOC.doctor(self.cfg)[0][1])
        for name in ("execute", "find", "still_signed", "on_state"):
            self.assertTrue(callable(getattr(FC, name)))

    def test_no_network_outside_graph_and_no_delete(self):
        code = Path(FC.__file__).parent
        for name in ("firstcomment", "reelextras"):
            text = (code / f"{name}.py").read_text()
            for word in ("urlopen", "urllib.request", "http.client", "_http(", '"DELETE"', "videos.delete",
                         "banAuthor"):
                self.assertNotIn(word, text, f"{name}.py: {word}")


# ------------------------------------------------------------------------------------------------------ review fixes
class ReviewFixesTest(Base):
    """2026-10-20 review: a lost answer after the post-id fallback, a slow Short and the caps, Pull then Approve
    after the post, links read as questions, the confirms, the signed card's line, the doctor's fixes."""

    def setUp(self):
        super().setUp()
        write_tokens(self.gta)
        self.scopes()

    def lost_after_fallback(self, video_comments) -> None:
        self.modes(first_comment__facebook_reels="on")
        s = self.with_words()
        vid = s["outlets"]["facebook_reels"]["post_id"]
        unsupported = meta_error(100, 33, msg="(#100) Unsupported post request. Object with ID '%s' does not "
                                              "support this operation" % vid)
        alt_posts = []

        def alt_post(call):
            alt_posts.append(call)
            if len(alt_posts) == 1:
                raise socket.timeout("timed out")                # the answer is lost; the comment went
            return {"id": "1000001_777_%d" % len(alt_posts)}
        g = (FakeGraph()
             .on("POST", rf"/{vid}/comments$", unsupported)
             .on("GET", rf"/{vid}$", {"post_id": "1000001_777", "id": vid})
             .on("POST", r"/1000001_777/comments$", alt_post)
             .on("GET", rf"/{vid}/comments$", video_comments(vid))
             .on("GET", r"/1000001_777/comments$", {"data": [
                 {"id": "1000001_777_1", "message": GTA_FB, "from": {"id": "1000001"}}]}))
        patch_http(self, graph=g)
        self.hook()
        self.clock.at("10:40")
        aid = self.items()[0]["id"]
        self.assertEqual(OW.send(self.cfg, self.gta, aid, self.clock())["state"], "sending")
        self.assertEqual(self.marker()["alt"], "1000001_777")            # noted before the post went there
        self.clock.advance(minutes=11)
        res = OW.send(self.cfg, self.gta, aid, self.clock())
        self.assertEqual(res["state"], "done", res)
        self.assertEqual(len(alt_posts), 1)                              # never twice
        self.assertEqual(self.marker()["id"], "1000001_777_1")
        self.assertEqual(g.paths()[-1], "GET /1000001_777/comments")

    def test_a_lost_answer_after_the_post_id_fallback_is_found_when_the_video_reads_empty(self):
        self.lost_after_fallback(lambda vid: {"data": []})

    def test_a_lost_answer_after_the_post_id_fallback_is_found_when_the_video_says_unsupported_get(self):
        self.lost_after_fallback(lambda vid: meta_error(100, 33, msg="(#100) Unsupported get request. Object with "
                                                                     "ID '%s' does not support this operation" % vid))

    def test_a_resend_goes_straight_to_the_post_id(self):
        self.modes(first_comment__facebook_reels="on")
        s = self.with_words()
        vid = s["outlets"]["facebook_reels"]["post_id"]
        g = (FakeGraph().on("GET", r"/comments$", {"data": []})
             .on("POST", r"/1000001_777/comments$", {"id": "1000001_777_5"}))
        patch_http(self, graph=g)
        self.hook()
        aid = self.items()[0]["id"]
        FC._set_marker(self.cfg, self.gta, DAY, K, "facebook_reels", {"alt": "1000001_777"})
        self.clock.at("10:40")
        self.assertEqual(OW.send(self.cfg, self.gta, aid, self.clock())["state"], "done")
        self.assertEqual(g.paths(), ["POST /1000001_777/comments"])
        self.assertNotIn(f"POST /{vid}/comments", g.paths())

    def drive(self, hours: float) -> None:
        """The 5-minute tick for a while: the sweep, then every action that is due."""
        for _ in range(int(hours * 12)):
            self.clock.advance(minutes=5)
            FC.sweep(self.cfg, now=self.clock())
            for it in OW.due(self.cfg, self.gta, self.clock()):
                OW.send(self.cfg, self.gta, it["id"], self.clock())

    def test_a_short_private_for_two_hours_leaves_the_next_shorts_first_comment_sendable(self):
        self.modes(first_comment__youtube_shorts="on")
        self.scopes(youtube=("youtube.upload", "youtube.force-ssl"))
        s1 = self.with_words()
        s2 = self.with_words(k="12:15")
        v1, v2 = s1["outlets"]["youtube_shorts"]["video_id"], s2["outlets"]["youtube_shorts"]["video_id"]

        def videos(call):
            vid = call["params"]["id"]
            return {"items": [{"id": vid, "status": {"privacyStatus": "public" if vid == v2 else "private"}}]}
        yt = FakeYouTube().on("GET", r"/videos$", videos).on("POST", r"/commentThreads$", {"id": "Ugz-2"})
        patch_http(self, graph=FakeGraph(), yt=yt)
        self.hook("youtube_shorts")
        self.drive(3)
        looks = [c for c in yt.calls if c["path"].endswith("/videos") and c["params"]["id"] == v1]
        self.assertLessEqual(len(looks), 4)                              # a few looks, not one every 5 minutes
        m = self.marker("youtube_shorts")
        self.assertEqual(m["state"], "skipped")
        self.assertIn("2 hours", m["why"])
        day_used = OW.counts(self.cfg, self.gta, "youtube", "first_comment", 86400, self.clock())
        self.assertLessEqual(day_used, 5)                                # the slow one's looks + the next one
        failed = list(OW.rows(self.cfg, self.gta, kinds=("first_comment",), states=("failed",)))
        self.assertEqual(failed, [])
        m2 = self.marker("youtube_shorts", k="12:15")                    # the next Short (the sweep queued it)
        self.assertEqual(m2["state"], "done", m2)
        self.assertEqual([c["path"] for c in yt.calls if c["method"] == "POST"], ["/commentThreads"])
        self.assertIn((True, "Gaming first comments on YouTube: on, 1 posted in 7 days, 1 did not go (the Log "
                             "says why)", ""), FC.doctor(self.cfg, self.clock()))

    def test_a_short_public_after_30_minutes_costs_one_look(self):
        self.modes(first_comment__youtube_shorts="on")
        self.scopes(youtube=("youtube.upload", "youtube.force-ssl"))
        s = self.with_words()
        vid = s["outlets"]["youtube_shorts"]["video_id"]
        live = FC._when(s["outlets"]["youtube_shorts"]["at"])
        status = {"privacyStatus": "private"}

        def videos(call):
            if self.clock() >= live + timedelta(minutes=30):
                status["privacyStatus"] = "public"
            return {"items": [{"id": vid, "status": dict(status)}]}
        yt = FakeYouTube().on("GET", r"/videos$", videos).on("POST", r"/commentThreads$", {"id": "Ugz-1"})
        patch_http(self, graph=FakeGraph(), yt=yt)
        self.hook("youtube_shorts")
        self.drive(1.5)
        self.assertEqual(self.marker("youtube_shorts")["state"], "done")
        self.assertEqual([c["method"] for c in yt.calls], ["GET", "GET", "POST"])
        self.assertEqual(OW.counts(self.cfg, self.gta, "youtube", "first_comment", 86400, self.clock()), 2)

    def test_pull_then_approve_again_after_the_post_sends_the_new_words_once(self):
        self.modes(first_comment__facebook_reels="on")
        g = FakeGraph().on("POST", r"/comments$", {"id": "c1"})
        patch_http(self, graph=g)
        self.with_words()
        self.clock.at("10:31")
        self.hook()
        S.pull(self.cfg, self.gta, DAY, [K], "Andrew")
        new = "Full video from Kyle: https://youtu.be/abc"
        FC.set_comment(self.cfg, self.gta, DAY, K, "facebook_reels", new, "Andrew")
        self.clock.at("10:40")
        (it,) = self.items()
        self.assertEqual(OW.send(self.cfg, self.gta, it["id"], self.clock())["state"], "skipped")
        self.assertIn("pulled", self.marker()["why"])
        self.assertEqual(FC.sweep(self.cfg, now=self.clock()), [])        # still pulled: nothing
        S.approve(self.cfg, self.gta, DAY, [K], "Andrew")
        self.assertIn(self.slot()["status"], B.SIGNED)
        self.assertEqual(FC.state_line(self.cfg, self.gta, self.slot(), "facebook_reels")[1],
                         "signed again: it queues within 5 minutes")
        self.clock.at("10:45")
        (line,) = FC.sweep(self.cfg, now=self.clock())
        self.assertIn("first comment queued", line)
        self.drive(0.5)
        self.assertEqual([c["form"]["message"] for c in g.calls], [new])  # once, the new words
        self.assertEqual(self.marker()["state"], "done")
        self.drive(0.5)
        self.assertEqual(len(g.calls), 1)

    def test_a_skip_after_a_call_is_never_queued_again(self):
        self.modes(first_comment__facebook_reels="on")
        g = FakeGraph().on("POST", r"/comments$", meta_error(100, msg="(#100) Comments are turned off"))
        patch_http(self, graph=g)
        self.with_words()
        self.hook()
        self.clock.at("10:40")
        self.drive(0.2)
        m = self.marker()
        self.assertTrue(m.get("called"))
        self.assertIn(m["state"], ("skipped", "gave_up", "queued"))
        self.assertFalse(FC._again(self.gta, "facebook_reels", dict(m, state="skipped", signed_at="earlier"),
                                   self.slot(), self.clock()))

    def test_a_link_in_a_caption_is_not_its_question(self):
        desc = self.tmp / "yt.txt"
        caption = "Kyle loses the cops on the bridge. Was this the best chase of the week?\n{url}\n"
        for url, deep in (("https://www.twitch.tv/videos/2290000000", "https://www.twitch.tv/videos/2290000000"
                                                                       "?t=0h3m10s"),
                          ("https://www.youtube.com/live/AbCdEf12345", "https://www.youtube.com/live/AbCdEf12345"
                                                                       "?t=190s")):
            desc.write_text(caption.format(url=deep))
            s = {"host": "Kyle", "source_url": url, "pick_span": [190, 228], "format": "underlay",
                 "copy": {"youtube_shorts": {"path": str(desc)}}}
            self.assertEqual(FC.questions(s), ["was this the best chase of the week"])
            got = FC.defaults(self.gta, s, FC.OUTLETS, DAY)
            self.assertEqual(set(got), set(FC.OUTLETS), url)
            self.assertIn(url, got["facebook_reels"])
            self.assertEqual(FC.check_text("facebook_reels", got["facebook_reels"], s), got["facebook_reels"])

    def test_the_save_confirm_quotes_the_words_and_the_box_id_carries_the_day(self):
        self.modes(first_comment__facebook_reels="on")
        with mock.patch.object(FC, "source", return_value=dict(SRC)):
            s = slot_ready(self, self.gta, DAY, K)[K]
        html = FC.card_part(self.cfg, self.gta, DAY, s, self.clock())
        quoted = f'Save this as the first comment under the Facebook post of Gaming 10:30: "{GTA_FB}"?'
        self.assertIn('data-ask="' + FC.esc(quoted), html)
        self.assertIn('data-ask-t="' + FC.esc(quoted.replace(GTA_FB, "{words}")), html)
        self.assertNotIn('oninput="', html)                 # the page's data-x handler fills {words} itself
        self.assertIn('name="text" data-w=""', html)
        self.assertIn('id="fc-gaming-20310708-1030-facebook_reels"', html)
        self.assertIn('for="fc-gaming-20310708-1030-facebook_reels"', html)

    def test_the_signed_card_says_why_a_post_gets_none(self):
        self.modes(first_comment__facebook_reels="on")
        self.with_words()
        self.clock.at("10:40")
        self.assertEqual(FC.state_line(self.cfg, self.gta, self.slot(), "facebook_reels")[1],
                         "queues within 5 minutes")
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"][K]["outlets"]["facebook_reels"]["provider"] = "matched"
        line = FC.state_line(self.cfg, self.gta, self.slot(), "facebook_reels")[1]
        self.assertTrue(line.startswith("none: not a post the factory made"), line)
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"][K]["outlets"]["facebook_reels"]["provider"] = "direct"
        self.clock.advance(hours=21)
        self.assertEqual(FC.state_line(self.cfg, self.gta, self.slot(), "facebook_reels")[1],
                         "none: more than 20 hours since it went live")

    def test_the_doctor_fixes_say_what_to_run(self):
        CAP.save_scopes(self.gta, "youtube", ["youtube.upload", "youtube.force-ssl"], "connect")
        rows = FC.doctor(self.cfg, self.clock())
        self.assertIn((None, "Gaming first comments on YouTube: off (try it: factory.py social on gaming "
                             "first_comment.youtube_shorts --practice)", ""), rows)
        fresh = self.cfg.account("politics")                       # permissions never read
        self.modes(acct=fresh, first_comment__facebook_reels="on")
        rows = FC.doctor(self.cfg, self.clock())
        probe = "factory.py social probe politics scopes"
        bad = [r for r in rows if r[0] is False and r[1].startswith("Politics first comments on Facebook")]
        self.assertEqual(bad, [(False, f"Politics first comments on Facebook: on, but it waits for its "
                                       f"permissions to be read ({probe})", probe)])


def _js_fn(js: str, name: str) -> str:
    """A top-level function of the page's script, by brace matching."""
    i = js.index(f"function {name}(")
    depth = 0
    for k in range(js.index("{", i), len(js)):
        depth += {"{": 1, "}": -1}.get(js[k], 0)
        if depth == 0:
            return js[i:k + 1]
    raise AssertionError(name)


class ApproveSignsWhatThePageShowedTest(Base):
    """2026-10-20 review (clipfactory/review): the card takes its seen key before it reads the extras file, and
    Approve stops on a first comment or poll box changed but not saved."""

    OLD = "From MrBossFTW on YouTube."
    NEW = "Something the page never showed."

    def test_a_write_while_the_card_renders_makes_its_key_stale(self):
        self.modes(first_comment__facebook_reels="practice")
        slot_ready(self, self.gta, DAY, K)
        FC.set_comment(self.cfg, self.gta, DAY, K, "facebook_reels", self.OLD, "Andrew")
        real = FC.card_part

        def card_part_then_a_write(*a, **kw):
            html = real(*a, **kw)
            FC.set_comment(self.cfg, self.gta, DAY, K, "facebook_reels", self.NEW, "task", how="task")
            return html
        with mock.patch.object(FC, "card_part", card_part_then_a_write):
            card = R._card(self.cfg, self.gta, DAY, self.slot(), self.clock())
        self.assertIn(self.OLD, card["html"])
        self.assertNotIn(self.NEW, card["html"])
        self.assertNotEqual(card["key"], B.seen_key(self.slot()))            # the page's key is stale now
        lines = S.approve(self.cfg, self.gta, DAY, [K], "Andrew", seen={K: card["key"]})
        self.assertIn("changed since the page showed it", "\n".join(lines))
        self.assertEqual(self.slot()["status"], "ready")                      # nothing signed
        fresh = R._card(self.cfg, self.gta, DAY, self.slot(), self.clock())  # the next draw shows NEW and signs it
        self.assertIn(self.NEW, fresh["html"])
        S.approve(self.cfg, self.gta, DAY, [K], "Andrew", seen={K: fresh["key"]})
        self.assertIn(self.slot()["status"], B.SIGNED)
        self.assertEqual(EX.first_comment(self.slot(), "facebook_reels"), self.NEW)

    def test_approve_stops_on_a_words_box_changed_but_not_saved(self):
        self.modes(first_comment__facebook_reels="practice", poll="practice")
        slot_ready(self, self.gta, DAY, K)
        html = R.page(self.cfg, DAY, self.clock())
        self.assertRegex(html, r'data-x-form=""[^>]*>(?:(?!</div></div>).)*<textarea [^>]*class="fc-t"[^>]*data-w=""')
        js = R.JS
        for line in ("if(op==='approve'){const c=b.closest('.card');if(holdUnsaved([c]))return;act(",):
            self.assertIn(line, js)
        allb = js[js.index("if(op==='approve-all'){"):js.index("if(op==='approve'){")]
        self.assertLess(allb.index("holdUnsaved(cards)"), allb.index("confirm("))
        self.assertLess(allb.index("holdUnsaved(cards)"), allb.index("act("))
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        script = _js_fn(js, "unsaved") + "\n" + _js_fn(js, "holdUnsaved") + r"""
const toasts = [];
function toast(m, bad) { toasts.push([m, !!bad]); }
const box = (value, def) => ({value, defaultValue: def, focused: false, focus() { this.focused = true; }});
const card = (slot, fields) => ({dataset: {slot}, querySelectorAll(sel) {
  if (sel !== '[data-x-form] [data-w]') throw new Error(sel); return fields; }});
const out = {};
out.clean = holdUnsaved([card('10:30', [box('saved words', 'saved words'), box('', '')])]);
out.space = holdUnsaved([card('10:30', [box('saved words  ', 'saved words')])]);
const typed = box('MY NEW WORDS', 'saved words');
out.one = holdUnsaved([card('10:30', [box('a', 'a'), typed])]);
out.focused = typed.focused;
out.many = holdUnsaved([card('10:30', [box('x', 'y')]), card('10:30', [box('x', 'y')]), card('11:00', [box('', 'opt')]),
                        card('12:00', [box('z', 'z')])]);
out.none = unsaved(null).length;
out.toasts = toasts;
console.log(JSON.stringify(out));
"""
        f = self.tmp / "unsaved.js"
        f.write_text(script)
        r = subprocess.run([node, str(f)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIs(out["clean"], False)
        self.assertIs(out["space"], False)
        self.assertIs(out["one"], True)
        self.assertIs(out["focused"], True)
        self.assertIs(out["many"], True)
        self.assertEqual(out["none"], 0)
        self.assertEqual(len(out["toasts"]), 2)
        self.assertTrue(out["toasts"][0][0].startswith("Not approved: 10:30 has words you changed but did not save"))
        self.assertTrue(out["toasts"][1][0].startswith("Not approved: 10:30, 11:00 have words"))
        self.assertTrue(all(bad for _, bad in out["toasts"]))


if __name__ == "__main__":
    unittest.main()
