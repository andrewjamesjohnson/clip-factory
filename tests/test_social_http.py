import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The social layer's way out (2026-10-19): direct._http keeps Meta's numbers and Google's reason as attributes with
# the message as it was, the rate-limit headers for last_usage(), graph.py's Meta and YouTube calls (the YouTube writes
# an allowlist of three, never anything stronger), the error classes, connect meta and connect youtube saving what the
# sign-in may do, the Instagram create form taking the signed extras. A fake urlopen; no network.

import email.message
import http.client
import io
import json
import re
import shutil
import socket
import sys
import threading
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import capabilities as CAP  # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import graph as G          # noqa: E402
from clipfactory import net                 # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory.stats import NotConnected  # noqa: E402

META_368 = {"error": {"message": "(#368) The action attempted has been deemed abusive or is otherwise disallowed",
                      "type": "OAuthException", "code": 368, "error_subcode": 1390008, "is_transient": False,
                      "fbtrace_id": "AbCdEf"}}
GOOGLE_QUOTA = {"error": {"code": 403, "message": "The request cannot be completed because you have exceeded your "
                                                  "<a href=\"/youtube/v3/getting-started#quota\">quota</a>.",
                          "errors": [{"message": "x", "domain": "youtube.quota", "reason": "quotaExceeded"}]}}
BUC = json.dumps({"1784": [{"type": "instagram", "call_count": 12, "total_cputime": 80, "total_time": 31,
                            "estimated_time_to_regain_access": 0}],
                  "2290": [{"type": "pages", "call_count": 3, "total_cputime": 1, "total_time": 2,
                            "estimated_time_to_regain_access": 7}]})


class Resp:
    def __init__(self, body: dict, headers=None):
        self.body, self.headers = json.dumps(body).encode(), headers

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Bare(Resp):
    """A response with no headers attribute at all (some tests' fakes are like that)."""

    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()


class FakeNet:
    """urllib.request.urlopen: each answer in turn (a Resp, or an (http status, body, headers) error)."""

    def __init__(self, *answers):
        self.answers, self.reqs = list(answers), []

    def __call__(self, req, timeout=0):
        self.reqs.append(req)
        a = self.answers.pop(0)
        if isinstance(a, tuple):
            code, body, headers = a
            raise urllib.error.HTTPError(req.full_url, code, "no", headers, io.BytesIO(json.dumps(body).encode()))
        if isinstance(a, BaseException):
            raise a
        return a


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-http-"))
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
        REG.reset()
        self.addCleanup(REG.reset)

    def tearDown(self):
        C.STATE = self.old_state
        O.TOKENS, D.TOKENS = self.old_tokens
        shutil.rmtree(self.tmp / "keys", ignore_errors=True)

    def wire(self, *answers) -> FakeNet:
        fake = FakeNet(*answers)
        p1 = mock.patch.object(urllib.request, "urlopen", fake)
        p2 = mock.patch.object(net, "guard", lambda what: None)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        return fake

    def inject(self, name: str, mod) -> None:
        key = "clipfactory." + name
        if key in sys.modules:
            self.addCleanup(sys.modules.__setitem__, key, sys.modules[key])
        else:
            self.addCleanup(sys.modules.pop, key, None)
        sys.modules[key] = mod
        REG.reset()

    def meta_keys_file(self) -> None:
        D._save(self.gta, "meta", {"page_id": "P1", "page_name": "Gaming", "page_token": "EAAPAGETOKEN123456",
                                   "ig_user_id": "IG1", "ig_username": "gaming", "app_id": "A1"})

    def yt_token_file(self, scope="https://www.googleapis.com/auth/youtube.upload") -> None:
        D.write_private(D._tok_path(self.gta, "youtube_token"), json.dumps({
            "client_id": "c", "client_secret": "s", "refresh_token": "r", "access_token": "ya29.FRESHTOKEN12345",
            "expires_at": 9e12, "scope": scope, "channel_id": "UC1", "channel_title": "Gaming",
            "channel_handle": "@gaming"}))


class HttpTest(Base):
    def test_1_meta_error_keeps_its_numbers_and_its_message(self):
        self.wire((400, META_368, {"X-Business-Use-Case-Usage": BUC}))
        with self.assertRaises(D.PlatformError) as ex:
            D._http("POST", f"{D.GRAPH}/17890/comments", form={"message": "hi", "access_token": "EAAxxxxxxxxxxxx"})
        e = ex.exception
        self.assertEqual(str(e), "HTTP 400 from graph.facebook.com: (#368) The action attempted has been deemed "
                                 "abusive or is otherwise disallowed")
        self.assertEqual((e.status, e.host, e.meta_code, e.meta_subcode, e.meta_type, e.transient, e.trace, e.reason),
                         (400, "graph.facebook.com", 368, 1390008, "OAuthException", False, "AbCdEf", None))
        self.assertEqual(D.last_usage(), {"buc_pct": 80.0, "regain_min": 7.0, "host": "graph.facebook.com"})

    def test_2_other_hosts_messages_are_byte_identical(self):
        self.wire((400, {"error": {"code": "spam_risk_too_many_posts", "message": "Too many posts"}}, {}),
                  (429, {"success": False, "message": "Limit", "error_code": "rate_limited"}, {}),
                  (400, {"error": "invalid_grant", "error_description": "Bad Request"}, None),
                  (500, {"error": {"message": "An unknown error", "code": "1"}}, {}))
        said = []
        for url in ("https://open.tiktokapis.com/v2/x", "https://api.upload-post.com/api/upload",
                    "https://oauth2.googleapis.com/token", f"{D.GRAPH}/x"):
            with self.assertRaises(D.PlatformError) as ex:
                D._http("POST", url, form={})
            said.append((str(ex.exception), ex.exception.meta_code, ex.exception.status))
        self.assertEqual(said, [
            ("HTTP 400 from open.tiktokapis.com: Too many posts [spam_risk_too_many_posts]", None, 400),
            ("HTTP 429 from api.upload-post.com: Limit [rate_limited]", None, 429),
            ("HTTP 400 from oauth2.googleapis.com: invalid_grant", None, 400),
            ("HTTP 500 from graph.facebook.com: An unknown error [1]", None, 500)])   # a code as text is not a number

    def test_3_google_reason(self):
        self.wire((403, GOOGLE_QUOTA, {}))
        with self.assertRaises(D.PlatformError) as ex:
            D._http("GET", "https://www.googleapis.com/youtube/v3/commentThreads", params={"part": "id"})
        self.assertEqual(ex.exception.reason, "quotaExceeded")
        self.assertIsNone(ex.exception.meta_code)
        self.assertTrue(str(ex.exception).startswith("HTTP 403 from www.googleapis.com: The request cannot be"))
        self.assertEqual(G.classify(ex.exception).kind, "quota")

    def test_4_usage_from_any_kind_of_headers_and_never_a_raise(self):
        msg = email.message.Message()
        msg["x-app-usage"] = json.dumps({"call_count": 41, "total_cputime": 5, "total_time": 9})
        msg["X-Page-Usage"] = "{not json"
        msg["Retry-After"] = "120"
        self.wire(Resp({"id": "1"}, msg), Resp({"id": "2"}, None), Bare({"id": "3"}),
                  Resp({"id": "4"}, {"x-business-use-case-usage": "[1, 2]"}),
                  Resp({"id": "5"}, {"X-Page-Usage": json.dumps({"call_count": 3})}))
        self.assertEqual(D._http("GET", f"{D.GRAPH}/me"), {"id": "1"})
        self.assertEqual(D.last_usage(), {"app_pct": 41.0, "retry_after": 120, "host": "graph.facebook.com"})
        D.last_usage()["app_pct"] = 0                                   # a copy: the caller cannot change it
        self.assertEqual(D.last_usage()["app_pct"], 41.0)
        D._http("GET", f"{D.GRAPH}/me")
        self.assertEqual(D.last_usage(), {})                            # this answer said nothing
        D._http("GET", f"{D.GRAPH}/me")
        self.assertEqual(D.last_usage(), {})
        D._http("GET", f"{D.GRAPH}/me")
        self.assertEqual(D.last_usage(), {})                            # a header of another shape is not there
        D._http("GET", f"{D.GRAPH}/me")
        self.assertEqual(D.last_usage(), {"page_pct": 3.0, "host": "graph.facebook.com"})
        D._note_usage("x", object())                                    # no .get at all
        self.assertEqual(D.last_usage(), {})

    def test_5_usage_is_per_thread(self):
        D._note_usage("graph.facebook.com", {"X-App-Usage": json.dumps({"call_count": 9})})
        seen = []
        t = threading.Thread(target=lambda: seen.append(D.last_usage()))
        t.start()
        t.join()
        self.assertEqual(seen, [{}])
        self.assertEqual(D.last_usage()["app_pct"], 9.0)

    def test_6_platform_error_and_its_subclasses_take_plain_words(self):
        for cls in (D.PlatformError, D.Refused, D.Pending, D.Stopped, D.LookupFailed):
            e = cls("words")
            self.assertEqual(str(e), "words")
            self.assertEqual([getattr(e, k) for k in D.PlatformError.INFO], [None] * 8)
        e = D.Refused("no", status=403, reason="forbidden")
        self.assertEqual((str(e), e.status, e.reason, e.meta_code), ("no", 403, "forbidden", None))


class ClassifyTest(unittest.TestCase):
    def kind(self, *a, **kw) -> str:
        return G.classify(D.PlatformError(*a, **kw)).kind

    def test_1_the_table(self):
        P = "HTTP 400 from graph.facebook.com: "
        rows = [
            ("block", (P + "x",), {"status": 400, "meta_code": 368}),
            ("block", (P + "Your account is temporarily blocked from commenting",), {"status": 400}),
            ("block", (P + "This Action Was Blocked. Please try again later.",), {"status": 400}),
            ("block", ("We restrict certain activity to protect our community",), {}),
            ("buc", (P + "x",), {"status": 400, "meta_code": 80002}),
            ("buc", (P + "x",), {"status": 400, "meta_code": 80006}),
            ("rate", (P + "x",), {"status": 400, "meta_code": 4}),
            ("rate", (P + "x",), {"status": 400, "meta_code": 17}),
            ("rate", (P + "x",), {"status": 400, "meta_code": 32}),
            ("rate", (P + "x",), {"status": 400, "meta_code": 613}),
            ("publish_limit", (P + "x",), {"status": 400, "meta_code": 9, "meta_subcode": 2207042}),
            ("publish_limit", (P + "x",), {"status": 400, "meta_code": 9}),
            ("perm", (P + "x",), {"status": 403, "meta_code": 10}),
            ("perm", (P + "x",), {"status": 403, "meta_code": 200}),
            ("perm", (P + "x",), {"status": 403, "meta_code": 299}),
            ("perm", (P + "(#200) Requires pages_manage_engagement permission",), {}),    # words only
            ("auth", (P + "x",), {"status": 401, "meta_code": 190}),
            ("auth", (P + "x",), {"status": 400, "meta_code": 102}),
            ("transient", (P + "x",), {"status": 500, "meta_code": 2, "transient": False}),
            ("transient", (P + "x",), {"status": 500, "meta_code": 1}),
            ("transient", (P + "x",), {"status": 500, "meta_code": 131000, "transient": True}),
            ("quota", ("HTTP 403 from www.googleapis.com: x",), {"status": 403, "reason": "dailyLimitExceeded"}),
            ("quota", ("x",), {"status": 403, "reason": "rateLimitExceeded"}),
            ("quota", ("x",), {"status": 403, "reason": "userRateLimitExceeded"}),
            # a youtube.readonly token reading a channel's comment threads, 2026-10-20: a missing permission
            ("perm", ("HTTP 403 from www.googleapis.com: Request had insufficient authentication scopes.",),
             {"status": 403, "reason": "insufficientPermissions"}),
            ("perm", ("HTTP 403 from www.googleapis.com: Request had insufficient authentication scopes.",), {}),
            ("perm", ("x",), {"status": 403, "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}),
            ("policy", ("x",), {"status": 403, "reason": "commentsDisabled"}),
            ("policy", ("x",), {"status": 403, "reason": "forbidden"}),
            ("policy", ("x",), {"status": 403, "reason": "ineligibleAccount"}),
            ("policy", ("x",), {"status": 400, "reason": "operationNotSupported"}),
            ("gone", ("HTTP 404 from www.googleapis.com: Not Found",), {"status": 404}),
            ("gone", (P + "Object with ID '1' does not exist",), {"status": 400, "meta_code": 100}),
            ("other", (P + "Object with ID '1' does not exist, cannot be loaded due to missing permissions",),
             {"status": 400, "meta_code": 100}),
            ("other", (P + "Invalid parameter",), {"status": 400, "meta_code": 100}),
            ("other", ("Instagram was still processing after 6 minutes",), {}),
        ]
        self.assertEqual([self.kind(*a, **kw) for _, a, kw in rows], [k for k, _, _ in rows])
        info = G.classify(D.PlatformError(P + "x", status=400, meta_code=368, meta_subcode=1, trace="T"))
        self.assertEqual(info, G.ErrorInfo("block", 368, 1, None, 400, P + "x", "T"))

    def test_1b_comments_without_their_words_are_a_missing_permission(self):
        # Instagram, 2026-10-20: a token without instagram_manage_comments gets id, timestamp, hidden and like_count
        stripped = [{"id": "c1", "timestamp": "t", "hidden": False, "like_count": 0}, {"id": "c2", "timestamp": "t"}]
        err = G.withheld("instagram", stripped)
        self.assertIsInstance(err, D.PlatformError)
        self.assertEqual(str(err), "Instagram gave 2 comments without the words or who wrote them: the token lacks "
                                   "instagram_manage_comments")
        self.assertEqual(G.classify(err).kind, "perm")
        self.assertIsNone(G.withheld("instagram", stripped + [{"id": "c3", "text": "", "username": "fan"}]))
        self.assertIsNone(G.withheld("instagram", []))                       # nothing to read proves nothing
        self.assertEqual(str(G.withheld("facebook", [{"id": "V_1", "created_time": "t"}])),
                         "Facebook gave 1 comment without the words or who wrote it: the token lacks "
                         "pages_read_user_content")
        self.assertIsNone(G.withheld("facebook", [{"id": "V_1", "message": "W"}]))   # anonymous, with its words
        self.assertIsNone(G.withheld("youtube", [{"id": "x"}]))

    def test_2_no_answer_is_a_timeout(self):
        for e in (urllib.error.URLError("refused"), socket.timeout("timed out"), TimeoutError("t"),
                  ConnectionResetError("r")):
            self.assertEqual(G.classify(e).kind, "timeout", e)
        self.assertEqual(G.classify(net.Offline("refused: offline")).kind, "other")
        self.assertEqual(G.classify(urllib.error.HTTPError("u", 404, "x", {}, None)).kind, "gone")

    def test_3_endpoint_has_no_ids(self):
        self.assertEqual(G.endpoint("post", f"{D.GRAPH}/178901234_998877/comments?access_token=EAA"),
                         "POST /{id}/comments")
        self.assertEqual(G.endpoint("GET", "17890000/replies"), "GET /{id}/replies")
        self.assertEqual(G.endpoint("GET", "https://www.googleapis.com/youtube/v3/commentThreads?part=id"),
                         "GET /youtube/v3/commentThreads")
        self.assertEqual(G.endpoint("GET", "/me/accounts"), "GET /me/accounts")


class GraphTest(Base):
    def test_1_meta_calls_carry_the_page_token(self):
        with self.assertRaises(NotConnected):
            G.meta_keys(self.gta)
        self.meta_keys_file()
        k = G.meta_keys(self.gta)
        self.assertEqual((k.page_id, k.ig_user_id, k.ig_username, k.app_id), ("P1", "IG1", "gaming", "A1"))
        calls = []

        def http(method, url, params=None, form=None, body=None, data=None, headers=None, timeout=120):
            calls.append((method, url, params, form, data, headers, timeout))
            return {"id": "C9"}
        with mock.patch.object(D, "_http", http):
            G.gget(self.gta, "IG1/media", {"fields": "id"})
            G.gpost(self.gta, "/17890/comments", form={"message": "Full video: x"})
            G.gpost(self.gta, "P1/photos", data=b"bytes", headers={"Content-Type": "multipart/form-data; boundary=b"})
        self.assertEqual(calls[0], ("GET", f"{D.GRAPH}/IG1/media", {"fields": "id", "access_token": k.page_token},
                                    None, None, None, 60))
        self.assertEqual(calls[1][:4], ("POST", f"{D.GRAPH}/17890/comments", None,
                                        {"message": "Full video: x", "access_token": k.page_token}))
        self.assertEqual(calls[2][2], {"access_token": k.page_token})
        self.assertEqual(calls[2][4], b"bytes")

    def test_1b_the_page_token_goes_nowhere_else_and_nothing_stronger_goes_out(self):
        self.meta_keys_file()
        calls = []

        def http(method, url, params=None, form=None, data=None, headers=None, timeout=120):
            calls.append((method, url, params, form))
            return {"id": "X"}
        with mock.patch.object(D, "_http", http):
            # a host that is not Meta's Graph: refused before the token is added
            for bad in ("https://example.com/collect", "http://graph.facebook.com/v25.0/me",
                        "https://graph.facebook.com.evil.example/x", "ftp://graph.facebook.com/x"):
                with self.assertRaises(SystemExit):
                    G.gget(self.gta, bad, {"x": 1})
                with self.assertRaises(SystemExit):
                    G.gpost(self.gta, bad, form={"message": "hi"})
            # Graph's method field (a verb in disguise), in any case and anywhere
            for form, path in (({"method": "delete"}, "17890011223344556"), ({"METHOD": "DELETE"}, "1/comments"),
                               ({"message": "hi"}, "1/comments?method=delete"), ({"Method ": "post"}, "1/comments")):
                with self.assertRaises(SystemExit):
                    G.gpost(self.gta, path, form=form)
            with self.assertRaises(SystemExit):
                G.gget(self.gta, "17890011223344556", {"method": "delete"})
            with self.assertRaises(SystemExit):
                G.gget(self.gta, "17890011223344556?Method=delete")
            with self.assertRaises(SystemExit):
                G.gpages(self.gta, "1/comments", {"method": "delete"})
            # a POST to an object itself only hides or shows a comment; anything else there is an edit
            for form in ({"description": "new words"}, {"is_hidden": "true", "message": "x"}, {}):
                with self.assertRaises(SystemExit):
                    G.gpost(self.gta, "17890011223344556", form=form)
            with self.assertRaises(SystemExit):
                G.gpost(self.gta, "P1/settings", form={"x": 1})
            with self.assertRaises(SystemExit):
                G.gpost(self.gta, "17890011223344556", data=b"bytes")
            self.assertEqual(calls, [])
            G.gpost(self.gta, "17890011223344556", form={"is_hidden": "true"})
            G.gpost(self.gta, "/17890", form={"hide": "true"})                 # Instagram hides with hide
            for edge in ("comments", "replies", "messages", "photos", "photo_stories", "video_stories", "feed",
                         "media", "media_publish", "private_replies"):
                G.gpost(self.gta, f"P1/{edge}", form={"x": "1"})
            G.gpost(self.gta, "https://rupload.facebook.com/video-upload/v25.0/123", data=b"bytes")
            G.gget(self.gta, f"{D.GRAPH}/P1/feed", {"after": "A"})
            G.gget(self.gta, "17890011223344556", {"fields": "id"})
        self.assertEqual(len(calls), 15)
        self.assertEqual(calls[-2][1], f"{D.GRAPH}/P1/feed")

    def test_1c_rupload_sends_the_token_only_in_the_header(self):
        """2026-10-20: a card's video bytes go to rupload as a Reel's do (direct._upload_bytes): OAuth header, offset,
        file_size, and no access_token in the address; only to an upload host."""
        self.meta_keys_file()
        calls = []

        def http(method, url, params=None, form=None, data=None, headers=None, timeout=120):
            calls.append((method, url, params, form, data, headers, timeout))
            return {"success": True}
        url = "https://rupload.facebook.com/video-upload/v25.0/123"
        with mock.patch.object(D, "_http", http):
            self.assertEqual(G.rupload(self.gta, url, b"abcd"), {"success": True})
            for bad in ("https://graph.facebook.com/v25.0/P1/feed", "http://rupload.facebook.com/x",
                        "https://example.com/upload", url + "?method=delete"):
                with self.assertRaises(SystemExit):
                    G.rupload(self.gta, bad, b"abcd")
        self.assertEqual(calls, [("POST", url, None, None, b"abcd", {"Authorization": "OAuth EAAPAGETOKEN123456",
                                                                    "offset": "0", "file_size": "4"}, 1800)])

    def test_2_pages_follow_the_cursor_and_stop(self):
        self.meta_keys_file()
        pages = [{"data": [{"id": "1"}, {"id": "2"}], "paging": {"cursors": {"after": "A"}, "next": "https://n"}},
                 {"data": [{"id": "3"}, {"id": "4"}], "paging": {"cursors": {"after": "B"}, "next": "https://n"}},
                 {"data": [{"id": "5"}], "paging": {"cursors": {"after": "C"}}}]
        seen = []

        def http(method, url, params=None, **kw):
            seen.append(dict(params))
            return pages[len(seen) - 1]
        with mock.patch.object(D, "_http", http):
            self.assertEqual([r["id"] for r in G.gpages(self.gta, "X/comments", {"limit": 2})],
                             ["1", "2", "3", "4", "5"])
        self.assertEqual([p.get("after") for p in seen], [None, "A", "B"])
        seen.clear()
        with mock.patch.object(D, "_http", http):
            self.assertEqual([r["id"] for r in G.gpages(self.gta, "X/comments", {}, stop=lambda r: r["id"] == "3")],
                             ["1", "2"])
        seen.clear()
        with mock.patch.object(D, "_http", http):
            self.assertEqual(len(G.gpages(self.gta, "X/comments", {}, max_pages=1)), 2)

    def test_3_multipart(self):
        f = self.tmp / "1.jpg"
        f.write_bytes(b"\xff\xd8JPEG")
        body, ctype = G.multipart({"published": "false"}, "source", f, "image/jpeg", '1".jpg')
        b = re.match(r"multipart/form-data; boundary=(\S+)$", ctype).group(1)
        self.assertEqual(body, (f'--{b}\r\nContent-Disposition: form-data; name="published"\r\n\r\nfalse\r\n'
                                f'--{b}\r\nContent-Disposition: form-data; name="source"; filename="1_.jpg"\r\n'
                                f'Content-Type: image/jpeg\r\n\r\n').encode() + b"\xff\xd8JPEG"
                         + f"\r\n--{b}--\r\n".encode())

    def test_4_youtube_writes_are_three_and_nothing_stronger(self):
        self.yt_token_file()
        calls, spent = [], []
        self.inject("outward", types.SimpleNamespace(yt_spend=lambda cfg, units, method, write, now=None:
                                                     spent.append((units, method, write))))

        def http(method, url, params=None, form=None, body=None, data=None, headers=None, timeout=120):
            calls.append((method, url, params, body, headers))
            return {"id": "Ug1"}
        thread = {"snippet": {"videoId": "v1", "topLevelComment": {"snippet": {"textOriginal": "Full video: x"}}}}
        with mock.patch.object(D, "_http", http), mock.patch.object(O, "_yt_access_token", lambda p: "ya29.T"):
            G.yt_write(self.gta, "commentThreads.insert", {}, thread, cfg=self.cfg)
            G.yt_write(self.gta, "comments.insert", {"part": "snippet"},
                       {"snippet": {"parentId": "Ug0", "textOriginal": "thanks"}})
            G.yt_write(self.gta, "comments.setModerationStatus", {"id": "Ug2", "moderationStatus": "heldForReview"})
            G.yt_write(self.gta, "comments.setModerationStatus", {"id": "Ug2", "moderationStatus": "published"})
            for method, params, body in (("videos.delete", {"id": "v1"}, None),
                                         ("videos.update", {"part": "status"}, {"snippet": {}}),
                                         ("playlistItems.delete", {"id": "p"}, None),
                                         ("comments.delete", {"id": "c"}, None),
                                         ("comments.setModerationStatus", {"id": "c", "moderationStatus": "rejected"},
                                          None),
                                         ("comments.setModerationStatus",
                                          {"id": "c", "moderationStatus": "heldForReview", "banAuthor": "true"}, None),
                                         ("comments.setModerationStatus", {"moderationStatus": "published"}, None),
                                         ("comments.insert", {}, None)):
                with self.assertRaises(SystemExit) as ex:
                    G.yt_write(self.gta, method, params, body)
                self.assertTrue(str(ex.exception).startswith("refused: "), method)
            G.yt_get(self.gta, "commentThreads", {"part": "id", "videoId": "v1"})
            with self.assertRaises(SystemExit):
                G.yt_get(self.gta, "search", {"q": "gta"})                 # 100 units: not in YT_COST
        self.assertEqual([(m, u.rsplit("/", 2)[-2:]) for m, u, *_ in calls],
                         [("POST", ["v3", "commentThreads"]), ("POST", ["v3", "comments"]),
                          ("POST", ["comments", "setModerationStatus"]), ("POST", ["comments", "setModerationStatus"]),
                          ("GET", ["v3", "commentThreads"])])
        self.assertEqual(calls[0][2], {"part": "snippet"})
        self.assertEqual(calls[0][3], thread)
        self.assertEqual(calls[2][2:4], ({"id": "Ug2", "moderationStatus": "heldForReview"}, None))
        self.assertEqual(calls[0][4], {"Authorization": "Bearer ya29.T"})
        self.assertEqual(spent, [(50, "commentThreads.insert", True), (50, "comments.insert", True),
                                 (50, "comments.setModerationStatus", True), (50, "comments.setModerationStatus", True),
                                 (1, "commentThreads.list", False)])

    def test_5_units_count_only_when_youtube_answered(self):
        self.yt_token_file()
        spent = []
        self.inject("outward", types.SimpleNamespace(yt_spend=lambda cfg, units, method, write, now=None:
                                                     spent.append(units)))
        with mock.patch.object(O, "_yt_access_token", lambda p: "ya29.T"):
            with mock.patch.object(D, "_http", side_effect=D.PlatformError("HTTP 403", status=403, reason="forbidden")):
                with self.assertRaises(D.PlatformError):
                    G.yt_get(self.gta, "videos", {"id": "v"})
            with mock.patch.object(D, "_http", side_effect=net.Offline("refused")):
                with self.assertRaises(net.Offline):
                    G.yt_get(self.gta, "videos", {"id": "v"})
            with mock.patch.object(D, "_http", side_effect=D.PlatformError("timed out")):
                with self.assertRaises(D.PlatformError):
                    G.yt_get(self.gta, "videos", {"id": "v"})
        self.assertEqual(spent, [1])
        self.inject("outward", types.SimpleNamespace(yt_spend=lambda *a, **k: 1 / 0))     # a broken counter
        with mock.patch.object(O, "_yt_access_token", lambda p: "ya29.T"), \
                mock.patch.object(D, "_http", return_value={"items": []}):
            self.assertEqual(G.yt_get(self.gta, "videos", {"id": "v"}), {"items": []})

    def test_6_youtube_not_connected_and_the_guard(self):
        with self.assertRaises(NotConnected):
            G.yt_get(self.gta, "videos", {})
        self.yt_token_file()
        tok = json.loads(D._tok_path(self.gta, "youtube_token").read_text())
        tok["expires_at"] = 0
        D.write_private(D._tok_path(self.gta, "youtube_token"), json.dumps(tok))
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")):
            with self.assertRaises(net.Offline):
                G.yt_get(self.gta, "videos", {})
            with self.assertRaises(net.Offline):
                G.yta_get(self.gta, {"ids": "channel==MINE"})
            self.meta_keys_file()
            with self.assertRaises(net.Offline):
                G.gget(self.gta, "me")


class NoStrongCallsTest(unittest.TestCase):
    def test_no_delete_or_stronger_call_anywhere(self):
        bad = re.compile(r"""["']DELETE["']|videos\.delete|videos\.update|playlistItems\.delete|banAuthor"""
                         r"""|(?i:method["']?\s*[:=]\s*["']?delete)""")
        found = []
        for p in sorted((ROOT / "clipfactory").glob("*.py")):
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if bad.search(line):
                    found.append(f"{p.name}:{i}: {line.strip()}")
        self.assertEqual(found, [])
        # the moderation statuses: only held for review or published, in the YouTube layer (bench and castqueue use
        # the word for their own states)
        for name in ("graph.py", "capabilities.py"):
            self.assertNotIn('"rejected"', (ROOT / "clipfactory" / name).read_text(), name)
        self.assertFalse([n for n in dir(G) if "delete" in n.lower() or n.lower().startswith("gdel")])


class ConnectTest(Base):
    def test_1_connect_meta_says_the_permissions_and_keeps_them(self):
        answers = {"debug_token": {"data": {"is_valid": True, "app_id": "A1",
                                            "scopes": ["pages_manage_posts", "instagram_basic"],
                                            "granular_scopes": [{"scope": "pages_read_user_content",
                                                                 "target_ids": ["P1"]},
                                                                {"scope": "instagram_basic"}]}},
                   "me/accounts": {"data": [{"id": "P1", "name": "Gaming", "access_token": "EAAPAGE123456789",
                                             "instagram_business_account": {"id": "IG1", "username": "gaming"}}]}}

        def http(method, url, params=None, **kw):
            return next(v for k, v in answers.items() if url.endswith(k))
        out = io.StringIO()
        with mock.patch.object(D, "_http", http), \
                mock.patch.object(D.getpass, "getpass", lambda p: "EAASHORT1234567"), \
                mock.patch("builtins.input", lambda p: "1"), mock.patch("sys.stdout", out):
            said = D.connect_meta(self.gta)
        self.assertIn("connected Gaming to Facebook Page Gaming", said)
        printed = out.getvalue()
        self.assertIn("for comments (Clip Review's Inbox and first comments), tick too: instagram_manage_comments, "
                      "pages_read_user_content\n", printed)
        self.assertIn("for messages, only after Meta's review: instagram_manage_messages, pages_messaging, "
                      "pages_manage_metadata\n", printed)
        self.assertEqual(CAP.scopes(self.gta, "meta"), ["instagram_basic", "pages_manage_posts",
                                                        "pages_read_user_content"])
        self.assertEqual(json.loads(CAP.file_path(self.gta).read_text())["meta"]["source"], "connect")
        self.assertNotIn("EAA", CAP.file_path(self.gta).read_text())            # names only, never a key

    def test_2_a_permission_list_that_cannot_be_kept_never_loses_the_sign_in(self):
        answers = {"debug_token": {"data": {"is_valid": True, "app_id": "A1", "scopes": ["x"]}},
                   "me/accounts": {"data": [{"id": "P1", "name": "Gaming", "access_token": "EAAPAGE123456789",
                                             "instagram_business_account": {"id": "IG1", "username": "gaming"}}]}}
        http = lambda m, url, **kw: next(v for k, v in answers.items() if url.endswith(k))      # noqa: E731
        with mock.patch.object(D, "_http", http), \
                mock.patch.object(D.getpass, "getpass", lambda p: "EAASHORT1234567"), \
                mock.patch("builtins.input", lambda p: "1"), mock.patch("sys.stdout", io.StringIO()), \
                mock.patch.object(CAP, "save_scopes", side_effect=SystemExit("refused: not under the state")):
            self.assertIn("connected Gaming", D.connect_meta(self.gta))
        self.assertTrue(D._tok_path(self.gta, "meta").exists())

    def test_3_save_youtube_keeps_the_scopes(self):
        tok = {"refresh_token": "r", "access_token": "a",
               "scope": "https://www.googleapis.com/auth/youtube.upload "
                        "https://www.googleapis.com/auth/youtube.readonly "
                        "https://www.googleapis.com/auth/youtube.force-ssl"}
        with mock.patch.object(D, "_http", return_value={"items": [
                {"id": "UC1", "snippet": {"title": "Gaming", "customUrl": "@gaming"}}]}):
            O.save_youtube(self.cfg, self.gta, {"client_id": "c", "client_secret": "s"}, tok)
        self.assertEqual(CAP.scopes(self.gta, "youtube"), ["youtube.force-ssl", "youtube.readonly", "youtube.upload"])
        self.assertEqual(CAP.missing(self.gta, "youtube", "comment"), [])
        self.assertEqual(CAP.missing(self.gta, "youtube", "analytics"), ["yt-analytics.readonly"])

    def test_4_connect_youtube_asks_for_more_only_with_extra_and_the_guard_comes_first(self):
        # since the watch-time fold every connect asks YouTube Analytics too (outlets.YT_SCOPES); --with analytics
        # is still accepted and asks for nothing more, and only --with comments adds force-ssl
        self.assertEqual(O.yt_scope(), "https://www.googleapis.com/auth/youtube.upload "
                                       "https://www.googleapis.com/auth/youtube.readonly "
                                       "https://www.googleapis.com/auth/yt-analytics.readonly")
        self.assertEqual(O.yt_scope(("analytics",)), O.yt_scope())
        self.assertEqual(O.yt_scope(("analytics", "comments")).split()[2:],
                         ["https://www.googleapis.com/auth/yt-analytics.readonly",
                          "https://www.googleapis.com/auth/youtube.force-ssl"])
        with self.assertRaises(SystemExit):
            O.yt_scope(("delete",))
        client = self.tmp / "client.json"
        client.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
        asked = []

        def browser(url):                   # Google sends the person back to the loopback with a code
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
            asked.append(q["scope"])
            port = urllib.parse.urlparse(q["redirect_uri"]).port
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/?" + urllib.parse.urlencode({"state": q["state"], "code": "4/xyz"}))
            c.getresponse().read()
            c.close()
            return True
        with mock.patch("webbrowser.open", browser), mock.patch("sys.stdout", io.StringIO()), \
                mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")):
            with self.assertRaises(net.Offline) as ex:
                O.connect_youtube(self.gta, client, self.cfg, extra=("comments",))
        self.assertIn("the YouTube sign-in", str(ex.exception))
        self.assertEqual(asked, ["https://www.googleapis.com/auth/youtube.upload "
                                 "https://www.googleapis.com/auth/youtube.readonly "
                                 "https://www.googleapis.com/auth/yt-analytics.readonly "
                                 "https://www.googleapis.com/auth/youtube.force-ssl"])
        self.assertFalse(D._tok_path(self.gta, "youtube_token").exists())


    def sign_in(self, extra=(), untick=()):
        """One connect youtube run: Google grants exactly the scopes this request asks for (no earlier grant carried
        over, the worst case) less the ones unticked at the prompt. Returns (what connect said, the request, printed)."""
        client = self.tmp / "client.json"
        client.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
        asked = []

        def browser(url):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
            asked.append(q)
            port = urllib.parse.urlparse(q["redirect_uri"]).port
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request("GET", "/?" + urllib.parse.urlencode({"state": q["state"], "code": "4/xyz"}))
            c.getresponse().read()
            c.close()
            return True

        def token(req, data=None, timeout=0):
            self.assertEqual(req if isinstance(req, str) else req.full_url, "https://oauth2.googleapis.com/token")
            scope = [x for x in asked[-1]["scope"].split() if x.rsplit("/", 1)[-1] not in untick]
            return Resp({"access_token": "ya29.NEW", "refresh_token": "r2", "expires_in": 3600,
                         "scope": " ".join(scope)})
        out = io.StringIO()
        with mock.patch("webbrowser.open", browser), mock.patch("sys.stdout", out), \
                mock.patch.object(urllib.request, "urlopen", token), mock.patch.object(net, "guard", lambda w: None), \
                mock.patch.object(D, "_http", return_value={"items": [
                    {"id": "UC1", "snippet": {"title": "Gaming", "customUrl": "@gaming"}}]}):
            said = O.connect_youtube(self.gta, client, self.cfg, extra=tuple(extra))
        return said, asked[-1], out.getvalue()

    def test_5_a_second_connect_with_one_extra_keeps_the_one_granted_first(self):
        # SETUP-POSTING once showed --with comments, then --with analytics: the second sign-in replaced the token
        # with one that had no youtube.force-ssl, and nothing said so
        self.sign_in(("comments",))
        self.assertEqual(CAP.missing(self.gta, "youtube", "comment"), [])
        said, q, printed = self.sign_in(("analytics",))
        self.assertEqual(q["include_granted_scopes"], "true")
        self.assertEqual(q["scope"].split()[2:], ["https://www.googleapis.com/auth/yt-analytics.readonly",
                                                  "https://www.googleapis.com/auth/youtube.force-ssl"])
        self.assertIn("Gaming already has comments on YouTube. Google asks for it again: leave it ticked to keep "
                      "it.", printed)
        self.assertEqual(CAP.missing(self.gta, "youtube", "comment"), [])
        self.assertEqual(CAP.missing(self.gta, "youtube", "analytics"), [])
        self.assertIn("youtube.force-ssl", json.loads(D._tok_path(self.gta, "youtube_token").read_text())["scope"])
        self.assertNotIn("no longer has", said)
        _, q, _ = self.sign_in()                    # a plain reconnect (a new Mac, a dead token) keeps both too
        self.assertEqual(len(q["scope"].split()), 4)
        self.assertEqual(CAP.yt_held(self.gta), ["comments", "analytics"])

    def test_6_an_extra_unticked_at_the_prompt_is_named_as_lost(self):
        self.sign_in(("comments",))
        said, _, _ = self.sign_in(("analytics",), untick=("youtube.force-ssl",))
        self.assertIn(". Gaming no longer has comments on YouTube, which it had before (unticked at the prompt): "
                      "connect again with --with comments,analytics and tick it to get it back", said)
        self.assertEqual(CAP.missing(self.gta, "youtube", "comment"), ["youtube.force-ssl"])
        said, _, _ = self.sign_in(("comments",), untick=("youtube.force-ssl",))
        self.assertIn(". Google did not grant comments (unticked at the prompt): connect again with --with "
                      "comments,analytics and tick it", said)

    def test_7_fix_lines_name_every_extra_in_one_with(self):
        from clipfactory import engagepage as EP
        from clipfactory import firstcomment as FC
        self.assertEqual(CAP.yt_connect_line(self.gta, "analytics"),
                         "factory.py connect youtube gaming --client client.json --with analytics")
        CAP.save_scopes(self.gta, "youtube", ["youtube.upload", "youtube.readonly", "yt-analytics.readonly"],
                        "connect")
        line = "factory.py connect youtube gaming --client client.json --with comments,analytics"
        self.assertEqual(CAP.yt_connect_line(self.gta, "comments"), line)
        self.assertEqual(FC._yt_connect(self.gta, "comments"), line)
        self.assertEqual(EP._yt_connect(self.gta, "comments"), line)
        CAP.save_scopes(self.gta, "youtube", ["youtube.upload", "youtube.force-ssl"], "connect")
        self.assertEqual(CAP.yt_connect_line(self.gta, "analytics", client="F"),
                         "factory.py connect youtube gaming --client F --with comments,analytics")
        self.assertEqual(CAP.yt_connect_line(self.gta),
                         "factory.py connect youtube gaming --client client.json --with comments")


class InstagramFormTest(Base):
    def post(self) -> dict:
        self.meta_keys_file()
        clip = self.tmp / "01-x.mp4"
        clip.write_bytes(b"mp4")
        created = {}

        def http(method, url, params=None, form=None, **kw):
            if url.endswith("/IG1/media") and method == "POST":
                created.update(form)
                return {"id": "C1", "uri": "https://rupload.facebook.com/x"}
            if url.endswith("/media_publish"):
                return {"id": "M1"}
            if "rupload" in url:
                return {}
            if url.endswith("/C1"):
                return {"status_code": "FINISHED"}
            return {"permalink": "https://instagram.com/reel/x", "timestamp": "2031-07-08T16:00:00+0000"}
        with mock.patch.object(D, "_http", http), mock.patch.object(D, "in_band", lambda *a, **k: None):
            D.ig_post(self.gta, clip, {"caption": "words"}, {"signoff": {"hash": "h"}})
        return created

    def test_1_extras_join_the_create_form(self):
        base = {"media_type": "REELS", "upload_type": "resumable", "caption": "words", "share_to_feed": "true",
                "access_token": "EAAPAGETOKEN123456"}
        self.assertEqual(self.post(), base)                          # extras.py not built: as it always went
        got = []
        self.inject("extras", types.SimpleNamespace(ig_form=lambda slot, acct: got.append(slot) or
                                                    {"trial_params": '{"graduation_strategy":"MANUAL"}'}))
        self.assertEqual(self.post(), dict(base, trial_params='{"graduation_strategy":"MANUAL"}'))
        self.assertEqual(got[0]["signoff"], {"hash": "h"})
        self.inject("extras", types.SimpleNamespace(ig_form=lambda slot, acct: 1 / 0))
        self.assertEqual(self.post(), base)                          # a broken part never stops a reel
        self.inject("extras", types.SimpleNamespace(ig_form=lambda slot, acct: ["not", "a", "dict"]))
        self.assertEqual(self.post(), base)


if __name__ == "__main__":
    unittest.main()
