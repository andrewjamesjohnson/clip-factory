import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The Log tab (2026-10-20): Actions with its filters, Health's success rate and time saved, Holds with Release only
# where Meta allows it, the Switches panel going through switches.set_mode and its checklist, "I read these", Stop
# everything in the header, the doctor's lines. Every click needs the page; a foreign Host or a stale token is 403.
# Throwaway folders; the platforms are fakes with no answers, so any call to one fails the test.

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

from social_fakes import FakeGraph, FakeYouTube, cfg_in_tmp, patch_http, serve  # noqa: E402

from clipfactory import capabilities as CAP  # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import logpage as LG       # noqa: E402
from clipfactory import outward as OW       # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import review as R         # noqa: E402
from clipfactory import social as SOC       # noqa: E402
from clipfactory import statspage as SP     # noqa: E402
from clipfactory import switches as SW      # noqa: E402

NOW = datetime(2031, 7, 8, 18, 0, tzinfo=timezone.utc)
DAY = "2031-07-08"


def part(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType("clipfactory." + name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class LogBase(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        self.http = patch_http(self, graph=FakeGraph(), yt=FakeYouTube())     # no routes: any call fails

    def tearDown(self):
        self.assertEqual((self.http.graph.calls, self.http.yt.calls), ([], []), "the Log tab called a platform")

    def inject(self, name: str, mod) -> None:
        p = mock.patch.dict(sys.modules, {"clipfactory." + name: mod})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(REG.reset)
        REG.reset()

    def rec(self, acct, plat, kind, state, hours_ago, mode="live", **kw):
        OW.record(self.cfg, acct, plat, kind, state=state, mode=mode, now=NOW - timedelta(hours=hours_ago), **kw)

    def fixture(self):
        fc = {"outlet": "facebook_reels", "key": "fc:facebook:1", "target": {"post_id": "934844", "board": DAY,
                                                                              "slot": "08:45"},
              "endpoint": "POST /{id}/comments", "text_sha": "abc123"}
        for i in range(3):
            self.rec(self.gta, "facebook", "first_comment", "done", 2 + i, result={"id": f"c{i}", "url":
                                                                                    "https://www.facebook.com/x"},
                     **fc)
        self.rec(self.gta, "facebook", "first_comment", "failed", 6,
                 error={"http": 400, "code": 100, "subcode": 33, "kind": "other", "msg": "Unsupported post request",
                        "trace": "AfakeTrace0001"}, usage={"buc_pct": 12, "regain_min": 0, "app_pct": 3}, **fc)
        self.rec(self.gta, "instagram", "reply_public", "done", 30, outlet="instagram_reels", text="Thanks")
        self.rec(self.gta, "instagram", "reply_public", "gave_up", 40, outlet="instagram_reels", why="Not sure it went")
        self.rec(self.politics, "facebook", "first_comment", "practice", 3, mode="practice", outlet="facebook_reels",
                 text="Full hearing on C-SPAN: https://www.c-span.org/x")
        self.rec(self.politics, "facebook", "first_comment", "practice", 50, mode="practice", outlet="facebook_reels",
                 text="Second practice words")
        self.rec(self.gta, "youtube", "first_comment", "done", 24 * 10, outlet="youtube_shorts")      # outside 7 days
        self.rec(self.gta, "facebook", "post", "done", 5, outlet="facebook_reels", key="post:facebook_reels:x")
        board = {"account": "gaming", "date": DAY, "slots": {"07:00": {
            "slot": "07:00", "publish_at": "2031-07-08T07:00:00-06:00", "status": "scheduled", "notes": [],
            "outlets": {
                "facebook_reels": {"post_id": "111", "url": "https://www.facebook.com/reel/111", "provider": "direct",
                                   "published_at": (NOW - timedelta(hours=5)).isoformat()},
                "instagram_reels": {"gave_up": True, "why": "Instagram refused the video", "tries": 3,
                                    "tried_at": (NOW - timedelta(hours=4)).isoformat()},
                "youtube_shorts": {"video_id": "yt1", "publish_at": (NOW - timedelta(hours=3)).isoformat()}}}}}
        p = C.STATE / "gaming" / f"{DAY}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(board))
        cards = C.STATE / "gaming" / "cards" / f"{DAY}.json"
        cards.parent.mkdir(parents=True, exist_ok=True)
        cards.write_text(json.dumps({"cards": {"countdown-0900": {
            "kind": "countdown", "status": "posted", "at": (NOW - timedelta(hours=9)).isoformat(),
            "files": {"images": [{"path": "x.jpg", "sha256": "s"}]}}}}))
        sc = C.STATE / "gaming" / "coach" / DAY / "0700.json"
        sc.parent.mkdir(parents=True, exist_ok=True)
        sc.write_text(json.dumps({"written": {"by": "clip-coach", "at": (NOW - timedelta(hours=10)).isoformat(),
                                              "scores": [7, 6, 5, 8, 6]}}))
        guard = C.STATE / "gaming" / "outward" / "guard.json"
        guard.write_text(json.dumps({"usage": {"facebook": {"buc_pct": 41.0, "regain_min": 0, "app_pct": 2.0,
                                                            "at": NOW.isoformat()}}}))


class ActionsTest(LogBase):
    def test_1_filters(self):
        self.fixture()

        def rows(**q):
            return LG.actions(self.cfg, q, NOW)["rows"]
        every = rows()
        self.assertEqual(len(every), 9)                        # 10 rows, one 10 days old
        self.assertEqual([r["t"] for r in every], sorted((r["t"] for r in every), reverse=True))   # newest first
        self.assertEqual(len(rows(window="30d")), 10)
        self.assertEqual(len(rows(window="24h")), 6)
        self.assertTrue(all(r["platform"] == "instagram" for r in rows(platform="instagram")))
        self.assertEqual(len(rows(platform="instagram")), 2)
        self.assertEqual(len(rows(kind="reply_public")), 2)
        self.assertEqual(len(rows(state="done")), 5)
        self.assertEqual(len(rows(mode="practice")), 2)
        self.assertEqual(len(rows(mode="live")), 7)
        self.assertEqual({r["acct"] for r in rows(acct="politics")}, {"politics"})
        got = rows(feature="first_comment.facebook_reels", mode="practice")
        self.assertEqual(len(got), 2)
        self.assertEqual(len(rows(platform="tiktok-nope", kind="nope", state="nope")), 9)     # unknown: no filter

    def test_2_a_row_opens_with_its_details(self):
        self.fixture()
        html = LG._actions_html(self.cfg, {"state": "failed"}, NOW)[0]
        self.assertIn("POST /{id}/comments", html)
        self.assertIn("code 100", html)
        self.assertIn("subcode 33", html)
        self.assertIn('data-copy="AfakeTrace0001"', html)
        self.assertIn("business use 12%", html)
        self.assertIn("sha256 abc123", html)                         # live rows: the digest, not the words
        self.assertIn(f'href="/?date={DAY}&amp;acct=gaming#c-gaming-0845"', html)
        practice = LG._actions_html(self.cfg, {"mode": "practice"}, NOW)[0]
        self.assertIn("Full hearing on C-SPAN: https://www.c-span.org/x", practice)       # practice: the words
        self.assertIn('class="lg-row"', practice)

    def test_3_busy_while_something_is_on_its_way(self):
        self.rec(self.gta, "facebook", "reply_public", "queued", 1, id="oa-1")
        self.assertTrue(LG.actions(self.cfg, {}, NOW)["busy"])
        self.rec(self.gta, "facebook", "reply_public", "done", 0.5, id="oa-1")
        self.assertFalse(LG.actions(self.cfg, {}, NOW)["busy"])

    def test_4_the_digest_note_says_how_long_the_words_stay(self):
        # A live row keeps its words (with @names) in the log for 90 days: the page must not say it keeps only a digest.
        self.assertEqual(LG.TEXT_DAYS, OW.LOG_TEXT_DAYS)
        words = "hi Pat, it is from MrBossFTW"
        OW.queue(self.cfg, self.gta, "instagram", "reply_private", key="pr:instagram:c1", text=words,
                 target={"comment_id": "c1", "to": "instagram:999"}, origin={"by": "page"},
                 signed={"what": "click", "by": "Andrew"}, now=NOW)

        def live_row():
            got = [r for r in OW.rows(self.cfg, self.gta.slug) if r.get("mode") == "live"]
            self.assertTrue(got)
            return got[0]

        row = live_row()
        self.assertEqual(row.get("text"), words)                     # the log really keeps the words
        html = LG._row_detail(self.cfg, dict(row, acct=self.gta.slug), NOW)
        self.assertNotIn("MrBossFTW", html)                          # the page shows only the digest
        self.assertNotIn("keep only the digest", html)               # and never claims the log drops the words
        self.assertIn(f"the words stay in the log for {OW.LOG_TEXT_DAYS} days", html)
        OW.prune_job(self.cfg, now=NOW + timedelta(days=OW.LOG_TEXT_DAYS + 1))
        row = live_row()
        self.assertNotIn("text", row)
        html = LG._row_detail(self.cfg, dict(row, acct=self.gta.slug), NOW + timedelta(days=OW.LOG_TEXT_DAYS + 1))
        self.assertIn(f"the words left the log after {OW.LOG_TEXT_DAYS} days", html)
        self.assertIn("sha256 ", html)


class HealthTest(LogBase):
    def test_1_success_rate_and_time_saved(self):
        self.fixture()
        h = LG.health(self.cfg, {}, NOW)
        rate = {(r["platform"], r["kind"]): r for r in h["rates"]}
        self.assertEqual(rate[("facebook", "first_comment")]["rate"], 0.75)
        self.assertEqual(rate[("instagram", "reply_public")]["rate"], 0.5)
        self.assertEqual(rate[("facebook", "post")]["done"], 1)          # from the board, not the guard's post row
        self.assertEqual(rate[("instagram", "post")]["gave_up"], 1)
        self.assertEqual(rate[("youtube", "post")]["rate"], 1.0)
        self.assertNotIn(("youtube", "first_comment"), rate)            # ten days old
        self.assertEqual(h["total"], {"done": 6, "failed": 1, "gave_up": 2, "rate": 6 / 9})
        self.assertEqual((h["sent"], h["practice"]), (4, 2))
        self.assertEqual(len(h["failed_posts"]), 1)
        self.assertEqual(h["failed_posts"][0]["why"], "Instagram refused the video")
        self.assertEqual(h["failed_posts"][0]["href"], f"/?date={DAY}&acct=gaming#c-gaming-0700")
        # posts 2 x 3, first comments 3 x 1, a public reply 0.5, a card made 10, a written scorecard 3
        self.assertEqual(h["minutes"], 22.5)
        self.assertEqual(h["usage"][0]["buc_pct"], 41.0)
        html = LG._health_html(self.cfg, {}, NOW)
        self.assertIn("Our estimate", html)
        self.assertIn("posts x 3 + first comments x 1 + public replies x 0.5 + private replies and messages x 1 + "
                      "hides x 0.25 + stories x 2 + card posts x 3 + cards made x 10 + written scorecards x 3", html)
        self.assertIn("Instagram refused the video", html)
        self.assertIn("67%", html)
        politics = LG.health(self.cfg, {"acct": "politics"}, NOW)
        self.assertEqual((politics["sent"], politics["practice"], politics["total"]["rate"]), (0, 2, None))

    def test_2_youtube_units(self):
        OW.yt_spend(self.cfg, 50, "commentThreads.insert", True, now=NOW)
        h = LG.health(self.cfg, {}, NOW)
        self.assertEqual(h["youtube"]["used"], 50)
        self.assertEqual(h["youtube"]["budget"], 2500)


class HoldsAndClicksTest(LogBase):
    def holds(self):
        p = C.STATE / "gaming" / "outward" / "holds.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"holds": {
            "instagram|comment": {"until": "2099-01-01T00:00:00+00:00", "why": "Meta said it was blocked (368)",
                                  "code": 368, "at": "2031-07-08T20:02:00+00:00", "by": "guard",
                                  "releasable": False, "kind": "block"},
            "facebook|publish": {"until": "2099-01-01T00:00:00+00:00", "why": "Meta blocked publishing (368)",
                                 "code": 368, "at": "2031-07-08T20:02:00+00:00", "by": "guard", "releasable": True,
                                 "kind": "block"}}}))
        return p

    def test_1_holds_view_words(self):
        self.holds()
        html = LG._holds_html(self.cfg, {}, NOW)
        self.assertIn("Meta asked us to stop: this one cannot be released early", html)
        self.assertIn("Meta asked us to wait (code 368 at 14:02). Release early? Gaming&#x27;s Facebook posts and "
                      "stories can go out again right away.", html)
        self.assertEqual(html.count('data-op="log-release"'), 1)

    def test_2_release_through_the_page_only(self):
        p = self.holds()
        get, post = serve(self, self.cfg)
        code, ans = post({"op": "log-release", "account": "gaming", "hold": "instagram|comment"})
        self.assertEqual(code, 400)
        self.assertIn("Meta asked us to stop: this one cannot be released early", ans["message"])
        code, ans = post({"op": "log-release", "account": "gaming", "hold": "facebook|publish"}, page=False)
        self.assertEqual(code, 400)
        self.assertIn("Only a click on the review page can release a hold.", ans["message"])
        code, ans = post({"op": "log-release", "account": "gaming", "hold": "facebook|publish"}, tok="stale")
        self.assertEqual((code, ans.get("stale")), (403, True))
        code, ans = post({"op": "log-release", "account": "gaming", "hold": "facebook|publish"})
        self.assertEqual(code, 200, ans)
        self.assertIn("released by Andrew", ans["message"])
        left = json.loads(p.read_text())["holds"]
        self.assertEqual(sorted(left), ["instagram|comment"])
        code, ans = post({"op": "log-release", "account": "stunts", "hold": "facebook|publish"})
        self.assertEqual(code, 400)                                     # switched off here: not his to click
        code, _, _ = get("/log", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)

    def test_3_the_page_and_its_data(self):
        self.holds()
        get, post = serve(self, self.cfg)
        for view in ("actions", "health", "holds", "switches"):
            code, headers, body = get(f"/log?view={view}")
            self.assertEqual(code, 200, view)
            self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
            html = body.decode()
            self.assertIn('<body class="sx sx-log">', html)
            self.assertIn('id="lg-view"', html)
            self.assertIn('<a href="/log" aria-current=page>Log</a>', html)
            code, _, body = get(f"/log/data?view={view}")
            d = json.loads(body)
            self.assertEqual((code, d["view"]), (200, view))
            self.assertTrue(d["stamp"] and d["html"])
            self.assertEqual(list(d)[-1], "stamp")
        self.assertIn("Holds (2)", get("/log")[2].decode())

    def test_4_stop_and_start_in_the_header(self):
        get, post = serve(self, self.cfg)
        html = get("/log")[2].decode()
        self.assertIn('data-op="social-stop" data-ask="Stop every comment, reply, message, story and card on every '
                      'account? Signed clips keep posting.">Stop everything</button>', html)
        self.assertIn('data-op="social-start"', html)
        self.assertEqual(post({"op": "social-stop"}, page=False)[0], 400)
        code, ans = post({"op": "social-stop"})
        self.assertEqual(code, 200, ans)
        html = get("/log")[2].decode()
        self.assertIn('class="lg-stop" data-op="social-stop"', html)
        self.assertRegex(html, r'data-op="social-stop"[^>]* hidden>Stop everything')
        self.assertIn("Stop everything is on", html)                       # the red line
        self.assertTrue(json.loads(get("/log/data")[2])["stopped"])
        self.assertEqual(post({"op": "social-start"})[0], 200)
        self.assertEqual(json.loads(get("/log/data")[2])["stopped"], "")


class ReviewFixesTest(LogBase):
    """The review of 2026-10-20: the filters redraw, the Release words, the waits words, the doctor's NO line."""

    def test_1_a_filter_change_redraws_though_the_select_has_focus(self):
        js = LG.JS
        change = js.split("document.addEventListener('change'", 1)[1].split("});", 1)[0]
        self.assertIn("s.blur(); refilter();", change)                   # the select lets go of the focus
        refilter = js.split("function refilter()", 1)[1].split("\n}", 1)[0]
        self.assertIn("forced = true; pager.now();", refilter)            # and the draw is told to take it
        self.assertIn("setTimeout(() => { forced = true; pager.now(); }", refilter)   # a read in flight was old
        draw = js.split("function draw(d)", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (!forced && a && v.contains(a)", draw)
        self.assertIn("waiting = d; return;", draw)                       # a skipped draw is kept
        self.assertIn("const d = waiting; if (d) draw(d);", js.split("addEventListener('focusout'", 1)[1])
        click = js.split("document.addEventListener('click'", 1)[1]
        self.assertIn("SX.setq(f.dataset.f, f.dataset.v); refilter();", click)
        # the filter selects really are inside the swapped view (which is why the guard must let go)
        self.fixture()
        html = LG.page(self.cfg, "t0k", R.CSS, R.tabs("log"), R.switch(self.cfg, "log", "all"), "all", {})
        view = html.split('id="lg-view"', 1)[1]
        self.assertIn('data-f="platform"', view)

    def test_2_release_words_say_who_paused_it(self):
        at = "2031-07-08T20:02:00+00:00"
        fails = {"until": "2099-01-01T00:00:00+00:00", "why": "3 failures in an hour", "code": 100, "at": at,
                 "releasable": True, "kind": "failures"}
        ask = LG.release_ask(self.cfg, self.gta, "facebook|first_comment", fails, NOW)
        self.assertEqual(ask, "We paused this ourselves after 3 failures in an hour (code 100 at 14:02). Release "
                              "early? Gaming's Facebook first comments can go out again right away.")
        auth = dict(fails, code=190, kind="auth")
        ask = LG.release_ask(self.cfg, self.gta, "instagram|comment", auth, NOW)
        self.assertTrue(ask.startswith("The sign-in stopped working (code 190 at 14:02). Release early?"), ask)
        self.assertIn("they fail until you connect again (factory.py connect meta gaming)", ask)
        self.assertNotIn("Meta asked", ask)
        block = dict(fails, code=368, kind="block")
        self.assertTrue(LG.release_ask(self.cfg, self.gta, "facebook|publish", block, NOW).startswith(
            "Meta asked us to wait (code 368 at 14:02). Release early?"))
        self.assertEqual(LG._hold_what(self.gta, "facebook|story"), "Facebook stories")
        self.assertEqual(LG._hold_what(self.gta, "instagram|reply_private"), "Instagram private replies")
        self.assertEqual(LG._hold_what(self.gta, "youtube|comment"), "YouTube comments and replies")
        for k in LG.KIND_WORDS:
            self.assertIn(k, LG.KIND_PLURAL)
            self.assertFalse(LG.KIND_PLURAL[k].endswith(("ys", "ss")), k)
        html = LG._holds_html(self.cfg, {}, NOW)
        self.assertNotIn("What Meta or YouTube asked us to pause", html)

    def test_3_waits_words_name_what_is_short(self):
        key = "first_comment.facebook_reels"
        SW.set_mode(self.cfg, self.politics, key, "practice", "Andrew", now=NOW - timedelta(days=3, hours=1))
        self.rec(self.politics, "facebook", "first_comment", "practice", 20, mode="practice", outlet="facebook_reels",
                 text="Full hearing on C-SPAN")
        st = LG.switch_state(self.cfg, self.politics, key, NOW)
        self.assertIn("practice: 1 of 10 rows, or 7 days (3 so far)", st["waits"])
        self.assertFalse([w for w in st["waits"] if "practice days" in w])
        SW.set_mode(self.cfg, self.gta, key, "practice", "Andrew", now=NOW - timedelta(hours=30))
        st = LG.switch_state(self.cfg, self.gta, key, NOW)
        self.assertIn("practice days (1 of 2), then 0 of 10 rows or 7 days", st["waits"])

    def test_4_the_doctor_line_comes_from_the_state(self):
        # On stays greyed while "I read these" is open, so the page never sends the refused click: the doctor
        # line comes from the switch's own state (practising, every other line done, rows waiting to be read)
        key = "first_comment.facebook_reels"
        self.inject("firstcomment", part("firstcomment"))
        CAP.save_scopes(self.gta, "meta", ["pages_manage_engagement", "pages_show_list"], "connect")
        SW.set_mode(self.cfg, self.gta, key, "practice", "Andrew", now=NOW - timedelta(days=8))
        self.assertEqual(LG.waits_for_reading(self.cfg, self.gta, NOW), [])      # 8 days, nothing to read yet
        self.rec(self.gta, "facebook", "first_comment", "practice", 5, mode="practice", outlet="facebook_reels",
                 text="Full video from MrBossFTW")
        items = SW.checklist(self.cfg, self.gta, key, NOW)
        self.assertEqual([d for d, _ in items], [True, True, False, True], items)
        on = [v for v in LG.switch_state(self.cfg, self.gta, key, NOW)["values"] if v["v"] == "on"][0]
        self.assertEqual(on["why"], "waits for the checklist below")      # greyed: no click can be refused
        line = (False, 'Gaming: practice rows wait for "I read these" (first_comment.facebook_reels)',
                "Clip Review, Log, Switches: read the practice rows of first_comment.facebook_reels, then press I "
                "read these")
        self.assertIn(line, LG.doctor(self.cfg, NOW))
        self.assertFalse((C.STATE / "gaming" / "social" / LG.TRIES).exists())    # no try was needed
        SW.ack_practice(self.cfg, self.gta, key, "Andrew", True, now=NOW)
        self.assertNotIn(line, LG.doctor(self.cfg, NOW))
        SW.set_mode(self.cfg, self.politics, key, "practice", "Andrew", now=NOW - timedelta(days=8))
        self.rec(self.politics, "facebook", "first_comment", "practice", 5, mode="practice", outlet="facebook_reels")
        self.assertEqual(LG.waits_for_reading(self.cfg, self.politics, NOW), [])   # its permission is missing too

    def test_5_phone_links_are_40px(self):
        phone = LG.CSS.split("@media (max-width:600px){", 1)[1]
        rule = [r for r in phone.split("\n") if "min-height:40px" in r and ".lg-ul a" in r]
        self.assertTrue(rule, phone)
        for sel in (".lg-check-b a", ".lg-more a", ".lg-ul a", ".lg-dl a"):
            self.assertIn(sel, rule[0])


class SwitchesTest(LogBase):
    def test_1_switch_op_goes_through_set_mode(self):
        get, post = serve(self, self.cfg)
        code, ans = post({"op": "log-switch", "account": "gaming", "key": "guard", "value": "watch"}, page=False)
        self.assertEqual(code, 400)
        self.assertEqual(SW.mode(self.gta, "guard"), "off")
        code, ans = post({"op": "log-switch", "account": "gaming", "key": "guard", "value": "watch"})
        self.assertEqual(code, 200, ans)
        self.assertEqual(SW.mode(self.gta, "guard"), "watch")
        self.assertEqual(SW.history(self.gta)[-1]["via"], "page")
        code, ans = post({"op": "log-switch", "account": "gaming", "key": "first_comment.facebook_reels",
                          "value": "on"})
        self.assertEqual(code, 400)
        self.assertIn("first_comment.facebook_reels stays off; before on: practice for 2 days", ans["message"])
        self.assertEqual(SW.mode(self.gta, "first_comment.facebook_reels"), "off")
        code, ans = post({"op": "log-switch", "account": "gaming", "key": "nope", "value": "on"})
        self.assertEqual(code, 400)

    def test_2_i_read_these_and_the_doctor(self):
        get, post = serve(self, self.cfg)
        key = "first_comment.facebook_reels"
        self.assertEqual(post({"op": "log-switch", "account": "gaming", "key": key, "value": "practice"})[0], 200)
        OW.record(self.cfg, self.gta, "facebook", "first_comment", state="practice", mode="practice",
                  outlet="facebook_reels", text="Full video from MrBossFTW")
        code, ans = post({"op": "log-switch", "account": "gaming", "key": key, "value": "on"})
        self.assertEqual(code, 400)
        rows = LG.doctor(self.cfg)
        self.assertIn((False, 'Gaming: practice rows wait for "I read these" (first_comment.facebook_reels)',
                       "Clip Review, Log, Switches: read the practice rows of first_comment.facebook_reels, then "
                       "press I read these"), rows)
        self.assertEqual(post({"op": "log-read-practice", "account": "gaming", "feature": key},
                              page=False)[0], 400)
        code, ans = post({"op": "log-read-practice", "account": "gaming", "feature": key})
        self.assertEqual(code, 200, ans)
        self.assertNotIn(False, [r[0] for r in LG.doctor(self.cfg)])
        item = [w for done, w in SW.checklist(self.cfg, self.gta, key) if done]
        self.assertIn('"I read these" pressed on the Log page after the last practice rows', item)

    def test_3_segments_say_why_they_wait(self):
        self.inject("engage", None)                                         # engage not built here
        html = LG._switches_html(self.cfg, {"acct": "gaming"}, NOW)
        self.assertIn("its part is not built here yet", html)
        self.inject("engage", part("engage"))
        self.inject("firstcomment", part("firstcomment"))
        st = LG.switch_state(self.cfg, self.gta, "replies", NOW)
        on = [v for v in st["values"] if v["v"] == "on"][0]
        self.assertIn("waits for permission: (not read yet: factory.py social probe gaming scopes)", on["why"])
        CAP.save_scopes(self.gta, "meta", ["pages_manage_engagement", "pages_show_list"], "connect")
        on = [v for v in LG.switch_state(self.cfg, self.gta, "replies", NOW)["values"] if v["v"] == "on"][0]
        self.assertEqual(on["why"], "")                                       # Facebook may reply: any one is enough
        SW.set_mode(self.cfg, self.gta, "first_comment.facebook_reels", "practice", "Andrew")
        st = LG.switch_state(self.cfg, self.gta, "first_comment.facebook_reels", NOW)
        on = [v for v in st["values"] if v["v"] == "on"][0]
        self.assertEqual(on["why"], "waits for the checklist below")
        html = LG._switches_html(self.cfg, {"acct": "gaming"}, NOW)
        self.assertIn('data-op="log-read-practice"', html)
        self.assertIn("Say you read the 0 practice rows of First comment on Facebook for Gaming?", html)
        self.assertIn('data-then="/log?view=actions&amp;acct=gaming&amp;feature=first_comment.facebook_reels'
                      '&amp;mode=practice&amp;window=30d"', html)
        self.assertIn("practice for 2 days and 10 rows (or 7 days)", html)
        self.assertRegex(html, r'data-value="on"[^>]* disabled title="waits for the checklist below"')
        self.assertIn("Switch Posting guard for Gaming from Off to Watch? It writes what it would pause in the "
                      "log; nothing is held.", html)
        self.assertIn("factory.py social set gaming warmup_days '28'", html)
        self.assertIn("Switch Posting guard for Gaming from Off to On? It pauses a platform&#x27;s posts", html)
        self.assertNotIn("Stunts", html)

    def test_4_every_switch_has_words(self):
        self.assertEqual(set(LG.PARTS), set(SW.MODES))
        self.assertEqual({k for _, ks in LG.GROUPS for k in ks}, set(SW.MODES))


class PageTest(LogBase):
    def test_1_phone_rules_and_structure(self):
        html = LG.page(self.cfg, "t0k", R.CSS + SP.CSS, R.tabs("log"), R.switch(self.cfg, "log", "all"), "all", {})
        self.assertIn("<meta name=viewport content='width=device-width,initial-scale=1'>", html)
        self.assertIn('<nav class="sx-subnav" aria-label="Views">', html)
        for view in ("Actions", "Health", "Holds", "Switches"):
            self.assertIn(f">{view}</a>", html)
        phone = LG.CSS.split("@media (max-width:600px){", 1)[1]
        self.assertIn(".lg-f select{height:40px;font-size:16px}", phone)
        self.assertIn("min-height:40px", phone)
        self.assertIn("body.sx .seg button{height:40px}", html)                # the kit's phone rule
        for css in (LG.CSS,):
            for w in re.findall(r"(?<![-\w])(?:min-)?width:(\d+)px", css):
                self.assertLessEqual(int(w), 248, "nothing wider than a 280px phone")
            self.assertNotRegex(css, r"gradient|box-shadow|glow")
        for text in (html, LG.CSS):
            self.assertNotIn("—", text)
            self.assertNotIn("–", text)
        self.assertNotRegex(re.sub(r"<script>.*?</script>", "", html, flags=re.S), r"[A-Za-z]!(\s|<)")

    def test_2_a_missing_outward_says_so(self):
        self.inject("outward", None)                    # sys.modules None: not built here
        self.assertFalse(REG.present("outward"))
        html = LG._actions_html(self.cfg, {}, NOW)[0]
        self.assertIn("The log is not built here yet", html)
        self.assertEqual(LG.doctor(self.cfg, NOW)[0], (None, "log: no actions in 7 days", ""))

    def test_3_doctor_line(self):
        self.fixture()
        rows = LG.doctor(self.cfg, NOW)
        self.assertEqual(rows[0], (None, "log: 11 actions in 7 days, success 67%", ""))
        self.assertTrue(all(r[0] is None for r in rows))
        doc = SOC.doctor(self.cfg, NOW)
        self.assertIn((None, "log: 11 actions in 7 days, success 67%", ""), doc)

    def test_4_unknown_op_and_client(self):
        with self.assertRaises(SystemExit):
            LG.act(self.cfg, {"op": "log-nope"}, page=True)
        with self.assertRaises(SystemExit):
            LG.act(self.cfg, {"op": "log-switch", "account": "nobody", "key": "guard", "value": "on"}, page=True)


if __name__ == "__main__":
    unittest.main()
