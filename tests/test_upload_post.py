"""TikTok through Upload-Post, against a fake Upload-Post: nothing here reaches the network (clipfactory/net.py)."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="clipfactory-uploadpost-"))
os.environ["CLIP_FACTORY_STATE"] = str(TMP / "state")

from clipfactory import direct as D     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
D.TOKENS = O.TOKENS = TMP / "keys"

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_factory import deliver        # noqa: E402

KEY = "eyJhbGciOiJIUzI1NiJ9.secretpart.signature"
TT = {"username": "-000openid-gta", "handle": "gaming", "display_name": "Gaming", "capabilities": ["draft", "music"]}


class FakeUploadPost:
    """Answers the few Upload-Post calls the factory makes. `history[rid]` and `status[rid]` say what an attempt id
    has done; an upload makes its id 'working' until `finish` turns it into a posted row."""

    def __init__(self):
        self.calls = []
        self.tiktok = dict(TT)
        self.privacy = ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]
        self.history = {}
        self.working = set()
        self.on_upload = "posted"        # what the next poll finds: posted / failed / inbox / working

    def __call__(self, method, url, params=None, form=None, body=None, data=None, headers=None, timeout=120):
        path = urllib.parse.urlparse(url).path.replace("/api", "", 1)
        self.calls.append((method, path, dict(params or {}), dict(headers or {}), data))
        assert (headers or {}).get("Authorization") == f"Apikey {KEY}"
        if path == "/uploadposts/me":
            return {"success": True, "plan": "Basic"}
        if path.startswith("/uploadposts/users/"):
            return {"success": True, "profile": {"username": path.rsplit("/", 1)[1], "social_accounts": {"tiktok": self.tiktok}}}
        if path == "/uploadposts/tiktok/settings":
            return {"success": True, "privacy_level_options": self.privacy}
        if path == "/uploadposts/history":
            rid = params["request_id"]
            if rid in self.working:
                self.working.discard(rid)                 # one poll later it has finished
                if self.on_upload != "working":
                    self.history[rid] = self._row(rid)
                return {"history": [], "in_progress": [{"request_id": rid}]}
            return {"history": [self.history[rid]] if rid in self.history else [], "in_progress": []}
        if path == "/uploadposts/status":
            raise D.PlatformError("HTTP 404 from api.upload-post.com: No upload request found with this ID")
        if path == "/upload":
            rid = self._field(data, "request_id")
            assert headers["Idempotency-Key"] == rid
            self.working.add(rid)
            return {"success": True, "request_id": rid, "message": "Upload initiated successfully in background."}
        raise AssertionError(f"unexpected call {method} {path}")

    def _row(self, rid):
        if self.on_upload == "failed":
            return {"platform": "tiktok", "success": False, "error_message": "TikTok said the video is too short"}
        if self.on_upload == "inbox":
            return {"platform": "tiktok", "success": True, "post_url": "Video sent to Inbox (No Public URL)",
                    "fallback_to_inbox": True, "request_id": rid}
        return {"platform": "tiktok", "success": True, "platform_post_id": "7401234567890123456",
                "post_url": "https://www.tiktok.com/@gaming/video/7401234567890123456",
                "upload_timestamp": "2030-01-03T14:00:05Z", "request_id": rid}

    @staticmethod
    def _field(data: bytes, name: str) -> str:
        head = f'name="{name}"\r\n\r\n'.encode()
        i = data.index(head) + len(head)
        return data[i:data.index(b"\r\n", i)].decode()

    def uploads(self):
        return [c for c in self.calls if c[1] == "/upload"]


class UploadPostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(TMP / "drafts")
        raw.pop("inbox_auto_sign", None)          # the owner's switches stay off here: these tests are the old rules
        for a in raw["accounts"].values():
            a.pop("signed_by_hand_stays", None)
            a["delivered"] = [str(TMP / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}
        cls.cfg_path = TMP / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))

    def setUp(self):
        self.up = FakeUploadPost()
        self.old = (D._http, D.in_band, D.UP_POLL, D.getpass.getpass)
        D._http, D.in_band, D.UP_POLL = self.up, lambda *a: None, 0
        D.getpass.getpass = lambda prompt="": KEY
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("gaming")
        self.clip = TMP / "clip.mp4"
        self.clip.write_bytes(b"\x00" * 64)
        self.slot = {"slot": "14:00", "publish_at": "2030-01-03T14:00:00-07:00", "signoff": {"hash": "abcdef0123456789"}}

    def tearDown(self):
        D._http, D.in_band, D.UP_POLL, D.getpass.getpass = self.old

    def connect(self):
        return D.connect_upload_post(self.acct, None, new_key=True)

    def test_1_connect_saves_the_key_hidden_and_names_the_tiktok_it_reaches(self):
        out = self.connect()
        self.assertIn("@gaming", out)
        self.assertIn("may post publicly", out)
        self.assertNotIn(KEY, out)
        for p in (D.TOKENS / "upload-post.json", D.TOKENS / "gaming" / "uploadpost.json"):
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600, p)
        link = json.loads((D.TOKENS / "gaming" / "uploadpost.json").read_text())
        self.assertEqual((link["profile"], link["tiktok_id"]), ("gaming", "-000openid-gta"))
        self.assertNotIn(KEY, json.dumps(link))                  # the key lives in one file only
        self.up.privacy = ["FOLLOWER_OF_CREATOR", "SELF_ONLY"]
        self.assertIn("may NOT post publicly", D.connect_upload_post(self.acct))

    def test_2_posts_once_public_with_the_cap_fallback_off(self):
        self.connect()
        res = D.up_tiktok_post(self.acct, self.clip, {"caption": "HE DID NOT SEE THE TRAIN #gta6"}, self.slot)
        self.assertEqual(res["post_id"], "7401234567890123456")
        self.assertTrue(res["url"].startswith("https://www.tiktok.com/@gaming/video/"))
        self.assertEqual(res["request_id"], "cf-gaming-203001031400-abcdef0123-0")
        (_, _, _, headers, data), = self.up.uploads()
        for field, value in (("user", "gaming"), ("platform[]", "tiktok"), ("post_mode", "DIRECT_POST"),
                             ("disable_inbox_fallback", "true"), ("title", "HE DID NOT SEE THE TRAIN #gta6")):
            self.assertEqual(self.up._field(data, field), value)
        self.assertNotIn(b'name="privacy_level"', data)          # a newer connection is public and takes no level
        again = D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
        self.assertEqual(again["post_id"], res["post_id"])
        self.assertEqual(len(self.up.uploads()), 1)              # the posted attempt is found, never sent twice

    def test_3_an_older_connection_is_asked_for_public(self):
        self.connect()
        self.up.tiktok = dict(TT, capabilities=["video_privacy", "inbox_fallback"])
        D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
        (_, _, _, _, data), = self.up.uploads()
        self.assertEqual(self.up._field(data, "privacy_level"), "PUBLIC_TO_EVERYONE")

    def test_4_refusals_that_trying_again_cannot_change(self):
        self.connect()
        self.up.tiktok = dict(TT, username="-000someone-else", handle="notgta")
        with self.assertRaises(D.Refused) as e:
            D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
        self.assertIn("different TikTok account (@notgta)", str(e.exception))
        self.up.tiktok = dict(TT)
        self.up.privacy = ["SELF_ONLY"]
        with self.assertRaises(D.Refused) as e:
            D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
        self.assertIn("may not post publicly", str(e.exception))
        self.up.privacy = ["PUBLIC_TO_EVERYONE"]
        self.up.on_upload = "inbox"
        with self.assertRaises(D.Refused) as e:
            D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
        self.assertIn("inbox as a draft", str(e.exception))
        self.assertEqual(len(self.up.uploads()), 1)              # the two refusals before it sent nothing

    def test_5_a_failure_is_a_try_and_the_next_try_takes_a_new_id(self):
        self.connect()
        self.up.on_upload = "failed"
        with self.assertRaises(D.PlatformError) as e:
            D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
        self.assertNotIsInstance(e.exception, (D.Refused, D.Pending))
        self.up.on_upload = "posted"
        res = D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
        self.assertTrue(res["request_id"].endswith("-1"))

    def test_6_still_publishing_waits_and_is_never_sent_again(self):
        self.connect()
        self.up.on_upload = "working"
        old = D.UP_POLLS
        D.UP_POLLS = 2
        try:
            with self.assertRaises(D.Pending):
                D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
            self.up.working.add("cf-gaming-203001031400-abcdef0123-0")
            with self.assertRaises(D.Pending):
                D.up_tiktok_post(self.acct, self.clip, {"caption": "x"}, self.slot)
        finally:
            D.UP_POLLS = old
        self.assertEqual(len(self.up.uploads()), 1)

    def test_7_keys_never_show_in_errors(self):
        self.assertNotIn("secretpart", D._redact(f"HTTP 401: bad token {KEY}"))

    def test_8_switched_on_today_leaves_earlier_slots_by_hand(self):
        day = date(2030, 1, 4)
        cfg = C.load(self.cfg_path)
        acct = cfg.account("gaming")
        dl = acct.delivered_dirs(day)[0]
        for hhmm, slug in (("0700", "before-one"), ("1400", "after-one")):
            deliver(dl, hhmm, slug, 2.0)
        K.collect(cfg, acct, day)
        S.approve(cfg, acct, day, None, "Andrew", now=cfg.slot_time(day, "06:00"))
        acct.outlets["tiktok"] = {"mode": "api", "via": "upload-post", "profile": "gaming",
                                  "from": cfg.slot_time(day, "12:00").isoformat()}
        O.hand_off(cfg, acct, day)
        O.export(cfg, acct, day, [s for s in B.read(acct, day)["slots"].values() if s.get("signoff")], dry_run=False)
        ready = K.drafts_dir(cfg, acct, day) / "Ready to post" / "tiktok"
        self.assertEqual(sorted(p.name for p in ready.glob("*.mp4")), ["0700 before-one.mp4"])
        self.assertTrue(O.covers(acct, "tiktok", cfg.slot_time(day, "14:00").isoformat()))
        self.assertFalse(O.covers(acct, "tiktok", cfg.slot_time(day, "07:00").isoformat()))
        calls = []
        fake = {"tiktok": lambda a, clip, words, slot: calls.append(slot["slot"]) or {"post_id": "tt", "url": "u"}}
        P.post_due(cfg, now=cfg.slot_time(day, "14:05"), posters=fake)
        self.assertEqual(calls, ["14:00"])                        # 07:00 was given to the hands, not posted twice

    def test_9_the_poster_counts_a_publish_still_running_as_no_try(self):
        day = date(2030, 1, 5)
        cfg = C.load(self.cfg_path)
        acct = cfg.account("gaming")
        acct.outlets["tiktok"] = {"mode": "api", "via": "upload-post", "profile": "gaming"}
        deliver(acct.delivered_dirs(day)[0], "0700", "slow-one", 2.0)
        K.collect(cfg, acct, day)
        S.approve(cfg, acct, day, None, "Andrew", now=cfg.slot_time(day, "06:00"))
        slow = {"tiktok": lambda *a: (_ for _ in ()).throw(D.Pending("Upload-Post has it and TikTok is still publishing"))}
        for i in range(4):
            P.post_due(cfg, now=cfg.slot_time(day, "07:00") + timedelta(minutes=10 * i), posters=slow)
        rec = B.read(acct, day)["slots"]["07:00"]["outlets"]["tiktok"]
        self.assertNotIn("tries", rec)
        self.assertNotIn("gave_up", rec)
        self.assertIn("still publishing", rec["waiting"])
        done = {"tiktok": lambda *a: {"post_id": "7401", "url": "https://www.tiktok.com/@gaming/video/7401"}}
        P.post_due(cfg, now=cfg.slot_time(day, "07:45"), posters=done)
        rec = B.read(acct, day)["slots"]["07:00"]["outlets"]["tiktok"]
        self.assertEqual(rec["post_id"], "7401")
        self.assertNotIn("waiting", rec)


if __name__ == "__main__":
    unittest.main()
