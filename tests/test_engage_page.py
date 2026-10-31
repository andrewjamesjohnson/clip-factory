import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The Inbox tab (engagepage.py), 2026-10-20: the page and its JSON are served with the CSP to the loopback only, a stale
# token is turned away, every op is refused unless a person clicked it on the page, a reply is queued through outward
# as a signed click (the exact words' sha, who, from the page) and the sender is kicked, the one private reply and the
# 23-hour message window are kept, the same words twice need "Send anyway", and nothing in the page or its JSON
# reaches a platform. The phone rules are there. Throwaway folders, the review page on a free port, fake platforms.

import json
import re
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import Clock, FakeGraph, FakeYouTube, cfg_in_tmp, patch_http, serve, write_tokens  # noqa: E402

from clipfactory import capabilities as CAP     # noqa: E402
from clipfactory import engage as E             # noqa: E402
from clipfactory import engagepage as EP        # noqa: E402
from clipfactory import outward as OW           # noqa: E402
from clipfactory import registry as REG         # noqa: E402
from clipfactory import review as R             # noqa: E402
from clipfactory import socialstore as SS       # noqa: E402
from clipfactory import store                   # noqa: E402
from clipfactory import switches as SW          # noqa: E402

META_ALL = ["instagram_basic", "instagram_manage_comments", "pages_read_engagement", "pages_read_user_content",
            "pages_manage_engagement", "instagram_manage_messages", "pages_messaging", "pages_manage_metadata"]


class PageTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.clock = Clock(self.now).patch(self, self.cfg)
        write_tokens(self.gta)
        self.g, self.yt = FakeGraph(), FakeYouTube()          # no routes: any platform call fails the test
        patch_http(self, graph=self.g, yt=self.yt)
        self.kick = mock.patch("clipfactory.social.kick", return_value="sending started")
        self.kicked = self.kick.start()
        self.addCleanup(self.kick.stop)
        self.get, self.post = serve(self, self.cfg)
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect")
        CAP.save_scopes(self.gta, "youtube", ["youtube.readonly", "youtube.force-ssl"], "connect")
        SW.set_mode(self.cfg, self.gta, "engage.read", "on", "Andrew")
        SW.set_mode(self.cfg, self.gta, "replies", "on", "Andrew")
        SW.set_setting(self.cfg, self.gta, "messages_access", "advanced", "Andrew")
        at = (self.now - timedelta(hours=2)).isoformat()
        SS.upsert_items(self.gta, [
            {"id": "ig:C1", "platform": "instagram", "kind": "comment", "platform_comment_id": "C1", "text": "source?",
             "at": at, "author": {"key": "instagram:111", "id": "111", "name": "fan_one", "is_us": False},
             "post": {"platform_id": "M1", "outlet": "instagram_reels", "board": "2031-07-08", "slot": "08:45",
                      "url": "https://www.instagram.com/reel/M1/", "title": "The chase"},
             "tags": ["question", "asks_source"], "suggest": {"hide": False, "kind": "", "why": ""}},
            {"id": "yt:Y1", "platform": "youtube", "kind": "comment", "platform_comment_id": "Y1", "text": "W",
             "at": at, "author": {"key": "youtube:UCfan", "id": "UCfan", "name": "Fan", "is_us": False},
             "post": {"platform_id": "v1", "outlet": "youtube_shorts"}, "tags": ["praise"]}], self.now)
        SS.upsert_thread(self.gta, {"id": "igt:T1", "platform": "instagram", "platform_thread_id": "T1",
                                    "with": {"key": "instagram:777", "id": "777", "name": "fan_dm"}},
                         [{"id": "m1", "at": (self.now - timedelta(hours=1)).isoformat(), "from_us": False,
                           "text": "where is this from"}], self.now)
        self.cover = Path(self.tmp) / "drafts" / "x" / "cover.jpg"
        self.cover.parent.mkdir(parents=True, exist_ok=True)
        self.cover.write_bytes(b"\xff\xd8fake")
        SS.set_posts_index(self.gta, {"M1": {"platform": "instagram", "outlet": "instagram_reels", "day": "2031-07-08",
                                             "slot": "08:45", "title": "The chase", "cover": str(self.cover)}})

    def act(self, op, page=True, **body):
        return self.post(dict({"op": op, "account": "gaming"}, **body), page=page)

    def queue(self):
        q = store.read(store.path("gaming", "outward", "queue.json"), {}) or {}
        return list((q.get("items") or {}).values())

    # ---------------------------------------------------------------------------------------------- serving
    def test_1_page_and_data_with_the_csp_loopback_only(self):
        code, headers, body = self.get("/inbox")
        self.assertEqual(code, 200)
        self.assertIn("img-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        html = body.decode()
        self.assertIn("<title>Clip Review · Inbox</title>", html)
        self.assertIn('<a href="/inbox" aria-current=page>Inbox</a>', html)
        self.assertIn('aria-current=page>Comments</a>', html)
        self.assertIn('href="/inbox?view=saved">Saved replies</a>', html)
        self.assertIn('id="ib-check"', html)
        self.assertIn('aria-current=page>Messages</a>', self.get("/inbox?view=messages")[2].decode())
        code, _, body = self.get("/inbox/data")
        d = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(d["stamp"])
        self.assertEqual([i["id"] for i in d["items"]], ["ig:C1", "yt:Y1"])
        c1 = d["items"][0]
        self.assertEqual(c1["post"]["clip"], "/?date=2031-07-08&acct=gaming#c-gaming-0845")
        self.assertTrue(c1["post"]["cover"].startswith("/file?p="))
        self.assertEqual(self.get(c1["post"]["cover"])[0], 200)                      # a drafts file: shown
        self.assertEqual((c1["author"]["initials"], c1["private_ok"], c1["limit"]), ("FO", True, 2200))
        self.assertEqual(d["threads"][0]["with"]["name"], "fan_dm")
        self.assertEqual(d["accounts"][0]["read"], "on")
        self.assertEqual(json.loads(self.get("/inbox/data")[2])["stamp"], d["stamp"])   # nothing changed, same stamp
        self.assertEqual(self.get("/inbox", {"Host": "evil.example"})[0], 403)
        self.assertEqual(self.get("/inbox/data", {"Host": "evil.example"})[0], 403)
        code, got = self.post({"op": "inbox-done", "account": "gaming", "item": "ig:C1"}, tok="stale")
        self.assertEqual((code, got.get("stale")), (403, True))
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "new")

    def test_2_every_op_needs_the_page(self):
        for op in EP.OPS:
            code, got = self.act(op, page=False, item="ig:C1", thread="igt:T1", text="hello", person="instagram:111",
                                 label="x", id="sr-1")
            self.assertEqual(code, 400, op)
            self.assertTrue(got["message"].startswith("Only a click on the review page can "), op)
        self.assertEqual(self.queue(), [])
        self.kicked.assert_not_called()
        code, got = self.act("inbox-nonsense")
        self.assertEqual(code, 400)
        self.assertIn("not a click the Inbox knows", got["message"])

    # ---------------------------------------------------------------------------------------------- sending
    def test_3_a_reply_is_a_signed_click_and_kicks_the_sender(self):
        code, got = self.act("inbox-reply", item="ig:C1", text="From MrBossFTW's stream", by="Andrew")
        self.assertEqual(code, 200, got)
        self.assertIn("Sending your reply to Instagram now", got["message"])
        self.kicked.assert_called_once()
        (a,) = self.queue()
        self.assertEqual((a["kind"], a["key"], a["platform"]), ("reply_public", "rpm:instagram:C1:1", "instagram"))
        self.assertEqual((a["signed"]["what"], a["signed"]["by"], a["signed"]["text_sha"]),
                         ("click", "Andrew", E._sha("From MrBossFTW's stream")))
        self.assertEqual((a["origin"]["via"], a["target"]["comment_id"], a["target"]["item"]), ("page", "C1", "ig:C1"))
        self.assertEqual(E.still_signed(self.cfg, self.gta, a), "")
        r = SS.get_item(self.gta, "ig:C1")["replies"][-1]
        self.assertEqual((r["state"], r["action"], r["text"]), ("queued", a["id"], "From MrBossFTW's stream"))
        self.assertEqual(json.loads(self.get("/inbox/data")[2])["items"][0]["replies"][0]["state"], "queued")
        # the same words again: refused with Send anyway, which a forced click may do
        self.clock.advance(minutes=1)
        OW._bump("gaming", "instagram", "reply_public", True, self.clock(), False, 0, "From MrBossFTW's stream")
        code, got = self.act("inbox-reply", item="ig:C1", text="From MrBossFTW's stream")
        self.assertEqual(code, 400)
        self.assertIn("Slow down", got["message"])
        self.assertIn("send it anyway", got["message"])
        code, got = self.act("inbox-reply", item="ig:C1", text="From MrBossFTW's stream", force=True)
        self.assertEqual(code, 200, got)
        self.assertEqual(self.queue()[-1]["key"], "rpm:instagram:C1:2")
        self.assertTrue(self.queue()[-1]["force"])
        self.assertEqual(self.g.calls, [])                                         # the page itself sends nothing

    def test_4_one_private_reply_and_its_window(self):
        code, got = self.act("inbox-reply", item="ig:C1", text="Here it is: the full stream", private=True)
        self.assertEqual(code, 200, got)
        a = self.queue()[-1]
        self.assertEqual((a["kind"], a["key"]), ("reply_private", "pr:instagram:C1"))
        code, got = self.act("inbox-reply", item="ig:C1", text="And another", private=True)
        self.assertEqual(code, 400)
        self.assertIn("one private reply", got["message"])
        code, got = self.act("inbox-reply", item="yt:Y1", text="thanks", private=True)
        self.assertIn("YouTube has no private replies", got["message"])
        old = (self.now - timedelta(days=7)).isoformat()
        SS.upsert_items(self.gta, [{"id": "ig:C9", "platform": "instagram", "kind": "comment",
                                    "platform_comment_id": "C9", "text": "x", "at": old,
                                    "author": {"key": "instagram:9", "id": "9", "name": "n"}}], self.now)
        code, got = self.act("inbox-reply", item="ig:C9", text="late", private=True)
        self.assertIn("within 6 days", got["message"])

    def test_5_messages_inside_23_hours_and_bytes(self):
        code, got = self.act("inbox-dm", thread="igt:T1", text="It is from the GTA RP server")
        self.assertEqual(code, 200, got)
        a = self.queue()[-1]
        self.assertEqual((a["kind"], a["key"], a["target"]["to_id"]),
                         ("dm", f"dmm:instagram:T1:{int(self.now.timestamp())}", "777"))
        self.assertEqual(SS.thread(self.gta, "igt:T1")["pending"][-1]["state"], "queued")
        code, got = self.act("inbox-dm", thread="igt:T1", text="x" * 1001)
        self.assertIn("1000 bytes", got["message"])
        self.clock.advance(hours=23, minutes=1)
        code, got = self.act("inbox-dm", thread="igt:T1", text="too late now")
        self.assertEqual(code, 400)
        self.assertIn("within 23 hours", got["message"])

    def test_6_replies_off_or_practice(self):
        SW.set_mode(self.cfg, self.gta, "replies", "off", "Andrew")
        code, got = self.act("inbox-reply", item="ig:C1", text="hello")
        self.assertEqual(code, 400)
        self.assertIn("Replies are off for Gaming", got["message"])
        SW.set_mode(self.cfg, self.gta, "replies", "practice", "Andrew")
        code, got = self.act("inbox-reply", item="ig:C1", text="hello")
        self.assertEqual(code, 200)
        self.assertIn("Practice: nothing goes to Instagram", got["message"])
        self.assertIn("No reply here", EP.JS)                                     # the page explains a closed box

    def test_7_hide_done_stop_tester_forget(self):
        code, got = self.act("inbox-hide", item="ig:C1")
        self.assertEqual(code, 200, got)
        self.assertEqual((self.queue()[-1]["kind"], self.queue()[-1]["key"]), ("hide", "hide:instagram:C1:on:1"))
        code, got = self.act("inbox-hide", item="yt:Y1")
        a = self.queue()[-1]
        self.assertEqual((a["kind"], a["target"]["status"]), ("yt_moderate", "heldForReview"))
        self.assertEqual(self.act("inbox-done", item="ig:C1")[0], 200)
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "done")
        self.act("inbox-undone", item="ig:C1")
        self.assertEqual(SS.get_item(self.gta, "ig:C1")["state"], "new")
        self.act("inbox-done", thread="igt:T1")
        self.assertEqual(SS.thread(self.gta, "igt:T1")["state"], "done")
        self.act("inbox-stop", item="ig:C1")
        self.assertTrue(SS.stopped(self.gta, "instagram:111"))
        self.act("inbox-stop", item="ig:C1", undo=True)
        self.assertFalse(SS.stopped(self.gta, "instagram:111"))
        code, got = self.act("inbox-tester", thread="igt:T1")
        self.assertEqual(code, 200, got)
        self.assertTrue(SW.is_tester(self.gta, "instagram", "777"))
        code, got = self.act("inbox-forget", person="instagram:111")
        self.assertEqual(code, 200, got)
        self.assertIsNone(SS.get_item(self.gta, "ig:C1"))
        self.assertEqual(self.queue()[-1]["kind"], "yt_moderate")                  # their queued hide went too

    def test_8_saved_replies_and_check_now(self):
        code, got = self.act("saved-set", label="Source", text="From the streamer's VOD", platforms=["instagram"])
        self.assertEqual(code, 200, got)
        (s,) = SS.saved(self.gta)
        self.assertEqual((s["label"], s["platforms"]), ("Source", ["instagram"]))
        self.assertEqual(json.loads(self.get("/inbox/data")[2])["saved"]["gaming"][0]["label"], "Source")
        self.act("saved-set", id=s["id"], label="Source", text="Changed words", platforms=["instagram", "facebook"])
        self.assertEqual(SS.saved(self.gta)[0]["text"], "Changed words")
        self.assertEqual(self.act("saved-delete", id=s["id"])[0], 200)
        self.assertEqual(SS.saved(self.gta), [])
        code, got = self.act("saved-set", label="", text="x")
        self.assertIn("needs a name", got["message"])
        code, got = self.act("inbox-check")
        self.assertEqual(code, 200)
        self.assertIn("Not started", got["message"])

    # ---------------------------------------------------------------------------------------------- reading only
    def test_9_no_network_in_the_page_or_its_data(self):
        with mock.patch("clipfactory.direct._http", side_effect=AssertionError("network in a GET")):
            for path in ("/inbox", "/inbox?view=messages", "/inbox?view=saved", "/inbox/data",
                         "/inbox/data?acct=gaming", "/inbox/suggest?acct=gaming&item=ig:C1"):
                self.assertEqual(self.get(path)[0], 200, path)
            EP.data(self.cfg, {})
        self.assertEqual((self.g.calls, self.yt.calls), ([], []))

    def test_10_suggest_from_answers_when_built(self):
        with mock.patch.dict(sys.modules, {"clipfactory.answers": None}):    # the automations part not there
            REG.reset()
            got = json.loads(self.get("/inbox/suggest?acct=gaming&item=ig:C1")[2])
        REG.reset()
        self.assertFalse(got["ok"])
        self.assertIn("automations part", got["message"])
        answers = types.ModuleType("clipfactory.answers")
        answers.suggest = lambda cfg, acct, item: {"text": "From MrBossFTW's stream", "why": "the clip's source"}
        with mock.patch.dict(sys.modules, {"clipfactory.answers": answers}):
            REG.reset()
            got = json.loads(self.get("/inbox/suggest?acct=gaming&item=ig:C1")[2])
            self.assertTrue(json.loads(self.get("/inbox/data")[2])["suggest"])
        REG.reset()
        self.assertEqual((got["ok"], got["text"]), (True, "From MrBossFTW's stream"))

    def test_11_phone_rules_and_the_look(self):
        html = self.get("/inbox")[2].decode()
        phone = EP.CSS.split("@media (max-width:600px)", 1)[1]
        for rule in (".ib-split.ib-open .ib-list{display:none}", ".ib-back{display:inline-flex",
                     ".ib-comp textarea{font-size:16px}", ".ib-bar .seg{flex-wrap:nowrap;overflow-x:auto"):
            self.assertIn(rule, phone)
        self.assertIn("body.sx button:not(.sx-row){height:auto;min-height:40px", html)  # the kit's 40px targets
        self.assertIn("body.sx select{height:40px", html)
        self.assertIn(".tabs{overflow-x:auto", html)                                        # tabs scroll
        self.assertIn('class="sx sx-inbox ib-comments"', html)
        for bad in ("gradient", "glow", "dotted"):
            self.assertNotIn(bad, EP.CSS)
        code = Path(EP.__file__).read_text() + Path(E.__file__).read_text()
        self.assertFalse(re.search("[\u2013\u2014]", code), "no long dashes in the Inbox's words")
        for s in re.findall(r"'([A-Z][^']{12,})'", EP.JS):                  # the words the page says
            self.assertNotIn("!", s, s)

    def test_12_the_tab_shows_on_every_page(self):
        self.assertIn('<a href="/inbox">Inbox</a>', R.tabs("clips", "all"))
        self.assertIn('<a href="/inbox?acct=gaming" aria-current=page>Inbox</a>', R.tabs("inbox", "gaming"))

    # ---------------------------------------------------------------------------------------------- review 2026-10-20
    def test_13_a_practised_private_reply_leaves_the_real_one(self):
        SW.set_mode(self.cfg, self.gta, "replies", "practice", "Andrew")
        code, got = self.act("inbox-reply", item="ig:C1", text="Here is the full stream", private=True)
        self.assertEqual(code, 200, got)
        res = OW.send(self.cfg, self.gta, self.queue()[-1]["id"], self.now)
        self.assertEqual(res["state"], "practice")
        self.assertEqual(self.g.calls, [])
        SW.set_mode(self.cfg, self.gta, "replies", "on", "Andrew")
        c1 = json.loads(self.get("/inbox/data")[2])["items"][0]
        self.assertEqual((c1["id"], c1["private_ok"]), ("ig:C1", True))
        code, got = self.act("inbox-reply", item="ig:C1", text="Here is the full stream", private=True)
        self.assertEqual(code, 200, got)
        self.assertIn("Sending your private reply", got["message"])

    def test_14_forced_practice_says_the_real_next_step(self):
        SW.set_setting(self.cfg, self.gta, "messages_access", "standard", "Andrew")
        code, got = self.act("inbox-reply", item="ig:C1", text="Here is the full stream", private=True)
        self.assertEqual(code, 200, got)
        self.assertIn("Messages reach only app testers until Meta's review", got["message"])
        self.assertIn("mark them a tester", got["message"])
        self.assertNotIn("Switch replies on", got["message"])
        code, got = self.act("inbox-dm", thread="igt:T1", text="It is from the GTA RP server")
        self.assertIn("if fan_dm is you", got["message"])
        self.assertNotIn("Switch replies on", got["message"])
        SW.set_mode(self.cfg, self.gta, "replies", "practice", "Andrew")
        code, got = self.act("inbox-reply", item="ig:C1", text="hello")
        self.assertIn("Switch replies on for Gaming to send for real", got["message"])

    def test_15_one_private_reply_per_person_a_week(self):
        at = (self.now - timedelta(hours=1)).isoformat()
        SS.upsert_items(self.gta, [{"id": "ig:C2", "platform": "instagram", "kind": "comment",
                                    "platform_comment_id": "C2", "text": "and the full one?", "at": at,
                                    "author": {"key": "instagram:111", "id": "111", "name": "fan_one"}}], self.now)
        code, got = self.act("inbox-reply", item="ig:C1", text="Here is the stream", private=True)
        self.assertEqual(code, 200, got)
        code, got = self.act("inbox-reply", item="ig:C2", text="And the full one", private=True)
        self.assertEqual(code, 400)
        self.assertIn("One private reply per person a week", got["message"])
        c2 = [i for i in json.loads(self.get("/inbox/data")[2])["items"] if i["id"] == "ig:C2"][0]
        self.assertFalse(c2["private_ok"])
        self.assertIn("per person a week", c2["private_why"])
        self.clock.advance(days=7, minutes=1)
        SS.upsert_items(self.gta, [{"id": "ig:C3", "platform": "instagram", "kind": "comment",
                                    "platform_comment_id": "C3", "text": "again", "at": self.clock().isoformat(),
                                    "author": {"key": "instagram:111", "id": "111", "name": "fan_one"}}], self.clock())
        code, got = self.act("inbox-reply", item="ig:C3", text="A week on", private=True)
        self.assertEqual(code, 200, got)

    def test_16_a_send_that_beats_the_page_is_never_put_back_to_queued(self):
        self.g.on("POST", r"^/C1/replies$", {"id": "R9"})
        self.g.on("POST", r"^/C1$", {"success": True})
        self.g.on("POST", r"^/1000001/messages$", {"recipient_id": "777", "message_id": "mid.9"})
        real = OW.queue

        def queue_and_send(*a, **kw):                    # the sender drains between the queue and the page's line
            aid = real(*a, **kw)
            OW.send(self.cfg, self.gta, aid, self.now)
            return aid
        with mock.patch.object(OW, "queue", side_effect=queue_and_send):
            self.assertEqual(self.act("inbox-reply", item="ig:C1", text="From MrBossFTW")[0], 200)
            self.assertEqual(self.act("inbox-dm", thread="igt:T1", text="From the RP server")[0], 200)
            self.assertEqual(self.act("inbox-hide", item="ig:C1")[0], 200)
        it = SS.get_item(self.gta, "ig:C1")
        replies = [(r["kind"], r["state"], r.get("text")) for r in it["replies"]]
        self.assertIn(("reply_public", "done", "From MrBossFTW"), replies)
        self.assertIn(("hide", "done", ""), replies)
        self.assertFalse(any(r["state"] == "queued" for r in it["replies"]), replies)
        pend = SS.thread(self.gta, "igt:T1")["pending"]
        self.assertEqual([(p["state"], p["by"], p["text"]) for p in pend], [("done", "Andrew", "From the RP server")])

    def test_17_the_data_reads_contacts_and_testers_once(self):
        rows = [{"id": f"ig:K{i}", "platform": "instagram", "kind": "comment", "platform_comment_id": f"K{i}",
                 "text": "hi", "at": (self.now - timedelta(minutes=i)).isoformat(),
                 "author": {"key": f"instagram:{9000 + i}", "id": str(9000 + i), "name": f"p{i}"}}
                for i in range(300)]
        SS.upsert_items(self.gta, rows, self.now)
        SS.stop_contact(self.gta, "instagram:9005", self.now)
        reads = []
        real_read = SS.read
        with mock.patch.object(SS, "read", side_effect=lambda a, n, d=None: (reads.append(n), real_read(a, n, d))[1]), \
                mock.patch.object(SS, "stopped", side_effect=AssertionError("one read per person")), \
                mock.patch.object(SW, "is_tester", side_effect=AssertionError("one read per person")):
            d = EP.data(self.cfg, {})
        self.assertEqual(reads.count("contacts"), 1)
        self.assertTrue([i for i in d["items"] if i["id"] == "ig:K5"][0]["author"]["stop"])

    def test_18_the_page_keeps_the_private_tick_shows_our_reply_once_and_sizes_links(self):
        self.assertIn("drafts['p:' + it.id]", EP.JS)
        self.assertIn("priv.checked = true", EP.JS)
        self.assertIn("said.splice(i, 1)", EP.JS)                                  # our reply read back: once
        self.assertIn("thr.sort(", EP.JS)                                           # oldest first
        phone = EP.CSS.split("@media (max-width:600px)", 1)[1]
        self.assertIn(".ib-postbox .ib-rb a{display:inline-flex;align-items:center;min-height:40px}", phone)

    def test_19_a_switched_off_part_has_no_line_on_the_page(self):
        SS.set_cursor(self.gta, "instagram", {"dm_error": "HTTPError: (#1) boom", "error": ""})
        SW.set_setting(self.cfg, self.gta, "engage.platforms", ["instagram"], "Andrew")
        SW.set_mode(self.cfg, self.gta, "engage.messages", "on", "Andrew")
        self.assertTrue(json.loads(self.get("/inbox/data")[2])["accounts"][0]["waits"])
        SW.set_mode(self.cfg, self.gta, "engage.messages", "off", "Andrew")
        self.assertEqual(json.loads(self.get("/inbox/data")[2])["accounts"][0]["waits"], [])

    # ---------------------------------------------------------------------------------------------- review 2026-10-20 (2)
    def _rule_private(self, cid, force_practice=False):
        """A rule's private reply the way rules._queue puts it: held by quiet hours, so only in the queue."""
        return OW.queue(self.cfg, self.gta, "instagram", "reply_private", key=f"pr:instagram:{cid}",
                        text="Here is the full stream: https://example.com/s",
                        target={"comment_id": cid, "item": f"ig:{cid}", "to": "instagram:111", "to_id": "111"},
                        origin={"by": "rule", "via": "rules", "rule": "r1"},
                        signed={"what": "rule", "by": "Andrew", "at": self.now.isoformat()},
                        not_before=self.now + timedelta(hours=5), force_practice=force_practice, now=self.now)

    def test_20_a_rules_queued_private_reply_counts_for_the_person(self):
        SS.upsert_items(self.gta, [{"id": "ig:C8", "platform": "instagram", "kind": "comment",
                                    "platform_comment_id": "C8", "text": "full?", "at": self.now.isoformat(),
                                    "author": {"key": "instagram:111", "id": "111", "name": "fan_one"}}], self.now)
        self._rule_private("C7", force_practice=True)          # practice never goes: it does not count
        self.assertEqual(EP._recent_private(self.cfg, self.gta, self.now), {})
        self._rule_private("C8")
        self.assertNotIn("private reply", str(SS.get_item(self.gta, "ig:C1").get("replies")))
        c1 = [i for i in json.loads(self.get("/inbox/data")[2])["items"] if i["id"] == "ig:C1"][0]
        self.assertFalse(c1["private_ok"])
        self.assertIn("per person a week", c1["private_why"])
        code, got = self.act("inbox-reply", item="ig:C1", text="And the full one", private=True)
        self.assertEqual(code, 400, got)
        self.assertIn("One private reply per person a week", got["message"])
        self.assertEqual([q["key"] for q in self.queue() if q["state"] == "queued" and not q["force_practice"]],
                         ["pr:instagram:C8"])
        code, got = self.act("inbox-reply", item="ig:C1", text="A public answer")      # a public reply still goes
        self.assertEqual(code, 200, got)

    def test_21_a_private_reply_that_went_counts_from_the_log(self):
        aid = self._rule_private("C8")
        item = OW._queue_get("gaming", aid)
        OW._queue_drop("gaming", aid)                       # gone from the queue: only the log's done row is left
        OW._row(self.cfg, "gaming", item, "done", "live", self.now)
        self.assertIn("instagram:111", EP._recent_private(self.cfg, self.gta, self.now))
        code, got = self.act("inbox-reply", item="ig:C1", text="And the full one", private=True)
        self.assertEqual(code, 400, got)

    def test_22_inbox_phone_and_tablet_widths_keep_to_the_screen(self):
        tag = re.search(r"\.ib-tag\{([^}]*)\}", EP.CSS).group(1)
        self.assertIn("max-width:100%", tag)                   # a long "Suggested hide: ..." tag wraps in its row
        self.assertNotIn("nowrap", tag)
        phone = EP.CSS.split("@media (max-width:600px)", 1)[1]
        self.assertIn(".ib-list{max-height:none;overflow:hidden}", phone)
        self.assertNotIn("overflow:visible", phone)
        split = re.search(r"\n\.ib-split\{([^}]*)\}", EP.CSS).group(1)
        self.assertIn("min(420px,40%)", split)                 # the reply pane keeps the larger share below ~1100px


if __name__ == "__main__":
    unittest.main()
