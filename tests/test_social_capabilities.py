import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# What each sign-in may do (capabilities.py), 2026-10-19: the permission names kept in state with no key, missing()
# for each need (an Account or a slug), a refusal marking a need missing until the next connect, the daily refresh
# only for an account with a social part on, the probes by hand, the doctor reading the file only (nothing with every
# part off), and the go-live checklist reading it. Throwaway folders; a fake Graph; no network.

import io
import json
import shutil
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import capabilities as CAP  # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import net                 # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import social as SOC       # noqa: E402
from clipfactory import switches as SW      # noqa: E402

NOW = datetime(2031, 7, 8, 16, 0, tzinfo=timezone.utc)
META_ALL = ["instagram_basic", "instagram_content_publish", "instagram_manage_comments", "instagram_manage_insights",
            "pages_manage_engagement", "pages_manage_posts", "pages_read_engagement", "pages_read_user_content",
            "pages_show_list", "read_insights"]


class FakeGraph:
    """D._http by the end of the url: an answer (a dict, or an exception to raise); every call kept."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def __call__(self, method, url, params=None, form=None, body=None, data=None, headers=None, timeout=120):
        self.calls.append((method, url, dict(params or {})))
        for k, v in self.answers.items():
            if url.endswith(k):
                if isinstance(v, BaseException):
                    raise v
                return v
        raise AssertionError(f"unexpected call {method} {url}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-caps-"))
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

    def tearDown(self):
        C.STATE = self.old_state
        O.TOKENS, D.TOKENS = self.old_tokens
        shutil.rmtree(self.tmp / "keys", ignore_errors=True)

    def signed_in(self, acct=None, yt_scope="https://www.googleapis.com/auth/youtube.upload "
                                            "https://www.googleapis.com/auth/youtube.readonly") -> None:
        acct = acct or self.gta
        D._save(acct, "meta", {"page_id": "P1", "page_name": acct.name, "page_token": "EAAPAGETOKEN123456",
                               "ig_user_id": "IG1", "ig_username": "x", "app_id": "A1"})
        D.write_private(D._tok_path(acct, "youtube_token"), json.dumps({
            "client_id": "c", "client_secret": "s", "refresh_token": "r", "access_token": "ya29.T", "expires_at": 9e12,
            "scope": yt_scope, "channel_id": "UC1"}))

    def debug(self, scopes, valid=True) -> dict:
        return {"data": {"is_valid": valid, "scopes": scopes[:-1],
                         "granular_scopes": [{"scope": s} for s in scopes[-1:]]}}


class StoreTest(Base):
    def test_1_scopes_are_names_and_missing_says_what_is_lacking(self):
        self.assertIsNone(CAP.scopes(self.gta, "instagram"))
        self.assertEqual(CAP.missing(self.gta, "instagram", "comment"),
                         ["(not read yet: factory.py social probe gaming scopes)"])
        CAP.save_scopes(self.gta, "meta", ["pages_read_engagement", "instagram_basic", " ", "instagram_basic"],
                        "connect", NOW)
        CAP.save_scopes("gaming", "youtube", ["https://www.googleapis.com/auth/youtube.force-ssl",
                                                 "https://www.googleapis.com/auth/youtube.upload"], "connect", NOW)
        d = json.loads(CAP.file_path(self.gta).read_text())
        self.assertEqual(d["meta"], {"scopes": ["instagram_basic", "pages_read_engagement"],
                                     "at": "2031-07-08T16:00:00+00:00", "source": "connect"})
        self.assertEqual(d["youtube"]["scopes"], ["youtube.force-ssl", "youtube.upload"])
        self.assertEqual(CAP.scopes(self.gta, "facebook"), ["instagram_basic", "pages_read_engagement"])
        self.assertEqual(CAP.missing(self.gta, "instagram", "read_comments"), ["instagram_manage_comments"])
        self.assertEqual(CAP.missing("gaming", "facebook", "read_comments"), ["pages_read_user_content"])
        self.assertEqual(CAP.missing(self.gta, "facebook", "message"), ["pages_messaging", "pages_manage_metadata"])
        self.assertEqual(CAP.missing(self.gta, "youtube", "read_comments"), [])     # force-ssl reads too
        self.assertEqual(CAP.missing(self.gta, "youtube", "comment"), [])
        self.assertEqual(CAP.missing(self.gta, "youtube", "analytics"), ["yt-analytics.readonly"])
        self.assertEqual(CAP.missing(self.gta, "youtube", "message"), ["(youtube has no message here)"])
        self.assertEqual(CAP.missing(self.politics, "youtube", "comment"),
                         ["(not read yet: factory.py social probe politics scopes)"])
        with self.assertRaises(SystemExit):
            CAP.save_scopes(self.gta, "tiktok", ["x"], "connect")
        for (plat, need), names in CAP.NEEDS.items():
            self.assertIn(plat, SW.PLATFORMS)
            self.assertIn(need, CAP.NEED_NAMES)
            self.assertTrue(names)

    def test_2_a_refusal_waits_for_the_next_connect(self):
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect", NOW)
        self.assertEqual(CAP.missing(self.gta, "instagram", "comment"), [])
        CAP.mark_missing(self.gta, "instagram", "first_comment", "(#200) Requires instagram_manage_comments", NOW)
        self.assertEqual(CAP.missing(self.gta, "instagram", "comment"),
                         ["(instagram refused it on 2031-07-08: connect meta again)"])
        self.assertEqual(CAP.missing(self.gta, "instagram", "moderate"), [])
        CAP.save_scopes(self.gta, "meta", META_ALL, "refresh", NOW)      # the daily read: Meta still refuses
        self.assertTrue(CAP.missing(self.gta, "instagram", "comment"))
        CAP.save_scopes(self.gta, "youtube", ["youtube.upload"], "connect", NOW)      # another platform's connect
        self.assertTrue(CAP.missing(self.gta, "instagram", "comment"))
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect", NOW)
        self.assertEqual(CAP.missing(self.gta, "instagram", "comment"), [])

    def test_3_probes_are_kept(self):
        self.assertIsNone(CAP.probe_result(self.gta, "ig_stories"))
        CAP.put_probe(self.gta, "ig_stories", "refused: (#10) not an Instagram Business", NOW)
        self.assertEqual(CAP.probe_result("gaming", "ig_stories"),
                         {"value": "refused: (#10) not an Instagram Business", "at": "2031-07-08T16:00:00+00:00"})
        CAP.file_path(self.gta).write_text("{broken")
        self.assertIsNone(CAP.probe_result(self.gta, "ig_stories"))
        self.assertEqual(CAP.missing(self.gta, "instagram", "comment"),
                         ["(not read yet: factory.py social probe gaming scopes)"])

    def test_4_the_checklist_reads_it(self):
        key = "first_comment.facebook_reels"
        item = lambda: SW.checklist(self.cfg, self.gta, key, NOW)[3]          # noqa: E731
        self.assertEqual(item(), (False, "the permission is there: missing (not read yet: factory.py social probe "
                                         "gaming scopes)"))
        CAP.save_scopes(self.gta, "meta", ["pages_read_engagement"], "connect", NOW)
        self.assertEqual(item(), (False, "the permission is there: missing pages_manage_engagement"))
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect", NOW)
        self.assertEqual(item(), (True, "the permission is there"))


class RefreshTest(Base):
    def test_1_refresh_reads_both_sign_ins(self):
        self.signed_in(yt_scope="https://www.googleapis.com/auth/youtube.upload "
                                "https://www.googleapis.com/auth/youtube.force-ssl")
        g = FakeGraph(debug_token=self.debug(["pages_manage_posts", "pages_read_engagement"]))
        with mock.patch.object(D, "_http", g):
            lines = CAP.refresh(self.cfg, self.gta)
        self.assertEqual(g.calls, [("GET", f"{D.GRAPH}/debug_token", {"input_token": "EAAPAGETOKEN123456",
                                                                      "access_token": "EAAPAGETOKEN123456"})])
        self.assertEqual(lines, ["Gaming Meta: 2 permissions: pages_manage_posts, pages_read_engagement",
                                 "Gaming YouTube: youtube.force-ssl, youtube.upload"])
        self.assertEqual(json.loads(CAP.file_path(self.gta).read_text())["meta"]["source"], "refresh")
        self.assertNotIn("EAA", CAP.file_path(self.gta).read_text())
        self.assertNotIn("ya29", CAP.file_path(self.gta).read_text())
        with mock.patch.object(D, "_http", FakeGraph(debug_token=self.debug([], valid=False))):
            self.assertIn("Gaming Meta: the Page token is not valid: run factory.py connect meta gaming",
                          CAP.refresh(self.cfg, self.gta))
        with mock.patch.object(D, "_http", FakeGraph(debug_token=D.PlatformError("HTTP 500 from graph: down"))):
            lines = CAP.refresh(self.cfg, self.gta)
        self.assertIn("Gaming Meta: could not read the permissions: HTTP 500 from graph: down", lines)
        self.assertIn("Gaming YouTube: youtube.force-ssl, youtube.upload", lines)       # the other still read
        self.assertEqual(CAP.refresh(self.cfg, self.politics), ["Politics Meta: not connected",
                                                              "Politics YouTube: not connected"])

    def test_2_refresh_due_only_for_parts_on_once_a_day_and_never_offline(self):
        self.signed_in()
        self.signed_in(self.politics)
        g = FakeGraph(debug_token=self.debug(["pages_manage_posts", "pages_read_engagement"]))
        with mock.patch.object(D, "_http", g):
            self.assertEqual(CAP.refresh_due(self.cfg, now=NOW), [])          # offline: nothing at all
            self.assertEqual(g.calls, [])
            with mock.patch.object(net, "why_not", lambda: ""):
                self.assertEqual(CAP.refresh_due(self.cfg, now=NOW), [])      # every part off: nothing read
                self.assertEqual(g.calls, [])
                SW.set_mode(self.cfg, self.gta, "guard", "watch", "Andrew")
                self.assertEqual(CAP.refresh_due(self.cfg, now=NOW), [])      # first read: nothing "changed"
                self.assertEqual(len(g.calls), 1)
                self.assertEqual(CAP.refresh_due(self.cfg, now=NOW + timedelta(hours=23)), [])
                self.assertEqual(len(g.calls), 1)                             # once a day
                g.answers["debug_token"] = self.debug(["pages_manage_posts", "pages_manage_engagement"])
                self.assertEqual(CAP.refresh_due(self.cfg, now=NOW + timedelta(hours=25)),
                                 ["Gaming Meta permissions changed: now has pages_manage_engagement; lost "
                                  "pages_read_engagement"])
                loud = CAP.refresh_due(self.cfg, now=NOW + timedelta(hours=50), quiet=False)
                self.assertEqual(loud[0], "Gaming Meta: 2 permissions: pages_manage_engagement, pages_manage_posts")
        self.assertIsNone(CAP.scopes(self.politics, "meta"))                    # the Politics has every part off

    def test_3_the_tick_runs_it(self):
        self.assertIs(REG.fn("capabilities:refresh_due"), CAP.refresh_due)
        self.assertEqual(SOC.run_job(self.cfg, "tick", quiet=True, now=NOW), [])


class ProbeTest(Base):
    def test_1_probes_by_hand(self):
        self.signed_in()
        with self.assertRaises(SystemExit):
            CAP.probe(self.cfg, self.gta, "everything")
        words = [{"id": "c1", "text": "source?", "username": "fan_one"}, {"id": "c2", "text": "W", "username": "fan"}]
        g = FakeGraph(**{"IG1/media": {"data": [{"id": "M9", "comments_count": 2}]}, "M9/comments": {"data": words},
                         "content_publishing_limit": {"data": [{"config": {"quota_total": 50, "quota_duration": 86400},
                                                                "quota_usage": 3}]},
                         "P1/conversations": D.PlatformError("HTTP 403 from graph.facebook.com: (#200) Requires "
                                                             "pages_messaging", status=403, meta_code=200)})
        with mock.patch.object(D, "_http", g):
            self.assertEqual(CAP.probe(self.cfg, self.gta, "ig-comments"),
                             ["Gaming ig-comments: Instagram comments are readable (2 read with their words and "
                              "names, on a post with 2)"])
            self.assertEqual(CAP.probe(self.cfg, self.gta, "publish-limit"),
                             ["Gaming publish-limit: Instagram publishes: 3 of 50 used in the last 24 hours"])
            self.assertEqual(CAP.probe(self.cfg, self.gta, "messages"),
                             ["Gaming messages: refused (perm): HTTP 403 from graph.facebook.com: (#200) Requires "
                              "pages_messaging"])
        self.assertTrue(all(p["access_token"] == "EAAPAGETOKEN123456" for _, _, p in g.calls))
        self.assertEqual(CAP.probe_result(self.gta, "publish-limit")["value"],
                         {"ok": True, "words": "Instagram publishes: 3 of 50 used in the last 24 hours", "kind": "",
                          "quota_total": 50, "quota_usage": 3})
        CAP.save_scopes(self.gta, "meta", META_ALL + ["pages_messaging", "pages_manage_metadata",
                                                      "instagram_manage_messages"], "refresh", NOW)
        self.assertEqual(CAP.missing(self.gta, "facebook", "message")[0][:26], "(facebook refused it on 20")
        self.assertTrue(CAP.missing(self.gta, "instagram", "message"))
        with mock.patch.object(D, "_http", FakeGraph(**{"P1/conversations": {"data": []}})):
            CAP.probe(self.cfg, self.gta, "messages")
        self.assertEqual(CAP.missing(self.gta, "facebook", "message"), [])      # it works now: the mark is gone

    def test_2_youtube_probes_count_their_units(self):
        self.signed_in()
        spent = []
        import types
        mod = types.SimpleNamespace(yt_spend=lambda cfg, units, method, write, now=None: spent.append((method, cfg)))
        if "clipfactory.outward" in sys.modules:          # outward.py is built since the seam's fourth commit
            self.addCleanup(sys.modules.__setitem__, "clipfactory.outward", sys.modules["clipfactory.outward"])
        else:
            self.addCleanup(sys.modules.pop, "clipfactory.outward", None)
        sys.modules["clipfactory.outward"] = mod
        REG.reset()
        g = FakeGraph(commentThreads={"items": [{"id": "t1"}]}, reports={"rows": [[1234]]})
        with mock.patch.object(D, "_http", g), mock.patch.object(O, "_yt_access_token", lambda p: "ya29.T"):
            self.assertEqual(CAP.probe(self.cfg, self.gta, "yt-comments"),
                             ["Gaming yt-comments: YouTube comments are readable (1 on the first page)"])
            self.assertEqual(CAP.probe(self.cfg, self.gta, "yt-analytics"),
                             ["Gaming yt-analytics: YouTube Analytics is readable (1234 views in the last 7 days)"])
        self.assertEqual(g.calls[0][2]["allThreadsRelatedToChannelId"], "UC1")
        self.assertEqual(spent, [("commentThreads.list", self.cfg)])          # Analytics has its own quota

    def test_3_offline_or_not_connected_is_said(self):
        self.assertEqual(CAP.probe(self.cfg, self.politics, "fb-comments"),
                         ["Politics fb-comments: not connected: run: factory.py connect meta politics"])
        self.signed_in()
        out = CAP.probe(self.cfg, self.gta, "fb-comments")                    # the real _http: the guard refuses
        self.assertEqual(out, ["Gaming fb-comments: could not ask (other): refused: GET graph.facebook.com would "
                               "reach a real account from a test or an offline run"])
        lines = CAP.probe(self.cfg, self.gta, "scopes")
        self.assertIn("Gaming Meta: could not read the permissions: refused: GET graph.facebook.com would reach a "
                      "real account from a test or an offline run", lines)
        self.assertIn("  youtube comment: missing youtube.force-ssl", lines)
        # 2026-10-20: commentThreads.list refuses a youtube.readonly token (403 insufficient scopes)
        self.assertIn("  youtube read comments: missing youtube.force-ssl", lines)


class CommentProbesTest(Base):
    """2026-10-20, read-only tests on both accounts: Instagram gave a token without instagram_manage_comments each
    comment's id and timestamp but no words or username, Facebook refused only a reel that HAS comments, YouTube
    refused youtube.readonly with a 403, and each probe still said "readable". A comments probe now passes only on
    comments read with their words; a no names what the kept sign-in lacks."""

    TODAY = ["business_management", "instagram_basic", "instagram_content_publish", "instagram_manage_insights",
             "pages_manage_engagement", "pages_manage_posts", "pages_read_engagement", "pages_show_list",
             "public_profile", "read_insights"]           # the token both accounts have on 2026-10-20

    def setUp(self):
        super().setUp()
        self.signed_in()
        CAP.save_scopes(self.gta, "meta", self.TODAY, "refresh", NOW)
        CAP.save_scopes(self.gta, "youtube", ["youtube.readonly", "youtube.upload"], "refresh", NOW)

    def probe(self, what, **answers):
        g = FakeGraph(**answers)
        with mock.patch.object(D, "_http", g), mock.patch.object(O, "_yt_access_token", lambda p: "ya29.T"):
            out = CAP.probe(self.cfg, self.gta, what)
        self.assertEqual(len(out), 1)
        return out[0], g, CAP.probe_result(self.gta, what)["value"]

    def test_1_instagram_without_the_words_is_a_missing_permission(self):
        stripped = {"data": [{"id": "c1", "timestamp": "2026-10-20T18:00:00+0000", "hidden": False,
                              "like_count": 0}]}
        line, g, kept = self.probe("ig-comments", **{
            "IG1/media": {"data": [{"id": "M1", "comments_count": 0}, {"id": "M2", "comments_count": 1}]},
            "M2/comments": stripped})
        self.assertEqual(line, "Gaming ig-comments: missing instagram_manage_comments: refused (perm): Instagram "
                               "gave 1 comment without the words or who wrote it: the token lacks "
                               "instagram_manage_comments")
        self.assertEqual([c[1].rsplit("/", 2)[-2:] for c in g.calls], [["IG1", "media"], ["M2", "comments"]])
        self.assertIn("comments_count", g.calls[0][2]["fields"])
        self.assertEqual(g.calls[1][2]["fields"], "id,text,username,timestamp")          # the post with a comment
        self.assertEqual((kept["ok"], kept["kind"], kept["missing"]), (False, "perm", ["instagram_manage_comments"]))
        self.assertEqual(CAP.missing(self.gta, "instagram", "read_comments")[0], "instagram_manage_comments")
        self.assertIn("instagram refused it", CAP.missing(self.gta, "instagram", "read_comments")[1])
        # the new token: the words come back, the probe passes and clears the refusal
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect", NOW)
        CAP.mark_missing(self.gta, "instagram", "read_comments", "x", NOW)
        line, _, kept = self.probe("ig-comments", **{
            "IG1/media": {"data": [{"id": "M1", "comments_count": 0}, {"id": "M2", "comments_count": 1}]},
            "M2/comments": {"data": [{"id": "c1", "text": "source?", "username": "fan_one"}]}})
        self.assertEqual(line, "Gaming ig-comments: Instagram comments are readable (1 read with their words and "
                               "names, on a post with 1)")
        self.assertEqual((kept["ok"], kept["proven"], kept["post"]), (True, True, "M2"))
        self.assertEqual(CAP.missing(self.gta, "instagram", "read_comments"), [])

    def test_2_no_comment_anywhere_is_not_proven_and_names_what_is_lacking(self):
        line, g, kept = self.probe("ig-comments", **{
            "IG1/media": {"data": [{"id": "M1", "comments_count": 0}, {"id": "M2", "comments_count": 0}]},
            "M1/comments": {"data": []}})
        self.assertEqual(line, "Gaming ig-comments: missing instagram_manage_comments: not proven: none of the "
                               "newest 2 posts has a comment to read yet (the newest read 0)")
        self.assertEqual((kept["ok"], kept["kind"], kept["proven"]), (False, "unproven", False))
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect", NOW)
        CAP.mark_missing(self.gta, "instagram", "read_comments", "x", NOW)
        line, _, _ = self.probe("ig-comments", **{"IG1/media": {"data": [{"id": "M1"}]}, "M1/comments": {"data": []}})
        self.assertEqual(line, "Gaming ig-comments: not proven: Instagram gave no comment counts, and the newest "
                               "post read 0")
        self.assertTrue(CAP.missing(self.gta, "instagram", "read_comments"))         # no proof clears nothing
        line, _, _ = self.probe("ig-comments", **{"IG1/media": {"data": [{"id": "M1", "comments_count": 3}]},
                                                  "M1/comments": {"data": []}})
        self.assertEqual(line, "Gaming ig-comments: not proven: Instagram counts 3 comments on post M1 but gave "
                               "none back")

    def test_3_facebook_reads_a_reel_that_has_comments(self):
        reels = {"data": [{"id": "V1", "comments": {"summary": {"total_count": 0}}},
                          {"id": "V2", "comments": {"summary": {"total_count": 21}}}]}
        refused = D.PlatformError("HTTP 400 from graph.facebook.com: (#200) Missing Permissions", status=400,
                                  meta_code=200)
        line, g, kept = self.probe("fb-comments", **{"P1/video_reels": reels, "V2/comments": refused})
        self.assertEqual(line, "Gaming fb-comments: missing pages_read_user_content: refused (perm): HTTP 400 from "
                               "graph.facebook.com: (#200) Missing Permissions")
        self.assertIn("comments.summary(true)", g.calls[0][2]["fields"])
        self.assertTrue(g.calls[1][1].endswith("/V2/comments"))                         # not the newest, V1
        self.assertEqual(kept["kind"], "perm")
        self.assertIn("facebook refused it", CAP.missing(self.gta, "facebook", "read_comments")[-1])
        # no reel has a comment: the newest is read, and that proves nothing
        reels = {"data": [{"id": "V1", "comments": {"summary": {"total_count": 0}}}]}
        line, g, kept = self.probe("fb-comments", **{"P1/video_reels": reels, "V1/comments": {"data": []}})
        self.assertEqual(line, "Gaming fb-comments: missing pages_read_user_content: not proven: none of the "
                               "newest 1 reels has a comment to read yet (the newest read 0)")
        self.assertEqual(g.calls[1][2]["filter"], "stream")
        # with the permission and the Page's task: read with the names
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect", NOW)
        reels = {"data": [{"id": "V1"}, {"id": "V2", "comments": {"summary": {"total_count": 2}}}]}
        line, _, kept = self.probe("fb-comments", **{"P1/video_reels": reels, "V2/comments": {"data": [
            {"id": "V2_1", "from": {"id": "9", "name": "Fan"}, "message": "where is this from?"}]}})
        self.assertEqual(line, "Gaming fb-comments: Facebook comments are readable (1 read with their words and "
                               "names, on a reel with 2)")
        self.assertEqual(CAP.missing(self.gta, "facebook", "read_comments"), [])
        line, _, _ = self.probe("fb-comments", **{"P1/video_reels": reels, "V2/comments": {"data": [{"id": "V2_1"}]}})
        self.assertIn("refused (perm): Facebook gave 1 comment without the words", line)

    def test_4_youtube_readonly_is_refused_and_says_force_ssl(self):
        no = D.PlatformError("HTTP 403 from www.googleapis.com: Request had insufficient authentication scopes.",
                             status=403, host="www.googleapis.com", reason="insufficientPermissions")
        line, _, kept = self.probe("yt-comments", commentThreads=no)
        self.assertEqual(line, "Gaming yt-comments: missing youtube.force-ssl: refused (perm): HTTP 403 from "
                               "www.googleapis.com: Request had insufficient authentication scopes.")
        self.assertEqual((kept["ok"], kept["kind"], kept["missing"]), (False, "perm", ["youtube.force-ssl"]))
        self.assertEqual(CAP.missing(self.gta, "youtube", "read_comments")[0], "youtube.force-ssl")
        CAP.save_scopes(self.gta, "youtube", ["youtube.readonly", "youtube.upload", "youtube.force-ssl"], "connect",
                        NOW)
        self.assertEqual(CAP.missing(self.gta, "youtube", "read_comments"), [])           # force-ssl holds readonly
        line, _, _ = self.probe("yt-comments", commentThreads={"items": []})
        self.assertEqual(line, "Gaming yt-comments: YouTube comments are readable (0 on the first page)")

    def test_5_a_refusal_with_every_permission_listed_points_at_the_page(self):
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect", NOW)
        refused = D.PlatformError("HTTP 400 from graph.facebook.com: (#200) Missing Permissions", meta_code=200)
        line, _, _ = self.probe("fb-comments", **{
            "P1/video_reels": {"data": [{"id": "V2", "comments": {"summary": {"total_count": 5}}}]},
            "V2/comments": refused})
        self.assertEqual(line, "Gaming fb-comments: refused (perm): HTTP 400 from graph.facebook.com: (#200) "
                               "Missing Permissions (the sign-in lists every permission named for it: see "
                               "SETUP-POSTING.md, the comments permissions, for the Page's task)")


class DoctorTest(Base):
    def doctor(self):
        with mock.patch.object(D, "_http", side_effect=AssertionError("network")):
            return CAP.doctor(self.cfg, NOW)

    def test_1_nothing_with_every_part_off(self):
        self.assertEqual(self.doctor(), [])
        SW.set_mode(self.cfg, self.gta, "guard", "watch", "Andrew")
        self.assertEqual(self.doctor(), [])                                   # the guard needs no permission

    def test_2_waits_in_practice_no_when_on(self):
        SW.set_mode(self.cfg, self.gta, "first_comment.facebook_reels", "practice", "Andrew")
        self.assertEqual(self.doctor(), [(None, "Gaming facebook comment: waits for (not read yet: factory.py "
                                                "social probe gaming scopes)", "")])
        SW.set_mode(self.cfg, self.gta, "checkpoints", "on", "Andrew")
        SW.set_mode(self.cfg, self.gta, "checkpoints.yt_analytics", "on", "Andrew")
        CAP.save_scopes(self.gta, "youtube", ["youtube.upload", "youtube.readonly"], "connect", NOW)
        rows = self.doctor()
        self.assertIn((False, "Gaming youtube analytics: waits for yt-analytics.readonly",
                       "factory.py connect youtube gaming --client client.json --with comments,analytics"), rows)
        CAP.save_scopes(self.gta, "youtube", ["youtube.upload", "yt-analytics.readonly"], "connect", NOW)
        CAP.save_scopes(self.gta, "meta", META_ALL, "connect", NOW)
        self.assertEqual(self.doctor(), [(True, "Gaming: every permission its social parts need is there (meta "
                                                "read 2031-07-08, youtube read 2031-07-08)", "")])

    def test_3_the_social_doctor_section_carries_it(self):
        SW.set_mode(self.cfg, self.gta, "engage.read", "on", "Andrew")
        rows = SOC.doctor(self.cfg, NOW)
        self.assertIn(False, [r[0] for r in rows if "instagram read comments" in r[1]])
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            SOC.cli(self.cfg, ["doctor"])
        self.assertIn("NO  Gaming instagram read comments: waits for (not read yet: factory.py social probe "
                      "gaming scopes)\n        fix: factory.py social probe gaming scopes\n", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
