"""TikTok's posting screen on the review page, and Post to TikTok now, against a fake TikTok: nothing here reaches
the network (clipfactory/net.py)."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)

import json
import os
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="clipfactory-ttpanel-"))
os.environ["CLIP_FACTORY_STATE"] = str(TMP / "state")

from clipfactory import direct as D     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
D.TOKENS = O.TOKENS = TMP / "keys"

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import review as R     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_factory import deliver        # noqa: E402

INFO = {"creator_nickname": "Gaming", "creator_username": "gamingp", "max_video_post_duration_sec": 600,
        "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
        "comment_disabled": False, "duet_disabled": False, "stitch_disabled": True}


class TikTokPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(TMP / "drafts")
        for a in raw["accounts"].values():
            a["delivered"] = [str(TMP / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"]["tiktok"] = {"mode": "export", "api_app_audited": False}
        cls.cfg_path = TMP / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        tok = D.TOKENS / "gaming" / "tiktok.json"
        tok.parent.mkdir(parents=True, exist_ok=True)
        tok.write_text(json.dumps({"client_key": "sbx", "client_secret": "s", "access_token": "act.x",
                                   "refresh_token": "r", "expires_at": time.time() + 86400, "open_id": "o"}))

    @classmethod
    def tearDownClass(cls):
        (D.TOKENS / "gaming" / "tiktok.json").unlink(missing_ok=True)   # other suites expect no TikTok key

    def setUp(self):
        self.old = (D.creator_info, P.spawn_tiktok_now)
        D.creator_info = lambda acct, max_age=60.0: dict(INFO)
        self.spawned = []
        P.spawn_tiktok_now = lambda cfg, acct, day, slot: self.spawned.append(slot) or True
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("gaming")

    def tearDown(self):
        D.creator_info, P.spawn_tiktok_now = self.old

    def day_with(self, day: date, hhmm: str, slug: str) -> dict:
        deliver(self.acct.delivered_dirs(day)[0], hhmm, slug, 2.0)
        K.collect(self.cfg, self.acct, day)
        slot = f"{hhmm[:2]}:{hhmm[2:]}"
        return B.read(self.acct, day)["slots"][slot]

    def act(self, day, op, slot, **kw):
        return R.act(self.cfg, dict(op=op, account="gaming", date=day.isoformat(), slots=[slot], by="Andrew", **kw))

    def panel(self, day, slot):
        return R._tt_panel(self.acct, B.read(self.acct, day)["slots"][slot])

    def test_1_the_screen_tiktok_asks_for(self):
        day = date(2030, 2, 1)
        self.day_with(day, "0700", "first")
        h = self.panel(day, "07:00")
        self.assertIn("Posting as <strong>Gaming</strong> @gamingp", h)
        self.assertIn('<option value="" selected disabled>Choose who can see this video</option>', h)  # no default
        self.assertNotIn(" checked", h)                                     # comments/duet/stitch all off
        self.assertIn('class="tt-check tt-greyed"', h)                      # stitch is off in the account: greyed
        self.assertIn("Music Usage Confirmation</a>", h)
        self.assertIn("Disclose video content", h)
        self.assertIn('class="tt-cap"', h)                                  # caption editable before signing
        self.assertNotIn("Post to TikTok now", h)                           # only a signed clip can go
        other = self.cfg.account("politics")
        self.assertEqual(R._tt_panel(other, B.read(self.acct, day)["slots"]["07:00"]), "")   # not connected: none

    def test_2_choices_are_saved_and_tiktok_rules_hold(self):
        day = date(2030, 2, 2)
        self.day_with(day, "0700", "choices")
        self.assertTrue(self.act(day, "tt-set", "07:00", field="privacy_level", value="SELF_ONLY")[0])
        self.assertFalse(self.act(day, "tt-set", "07:00", field="privacy_level", value="EVERYONE_PLEASE")[0])
        self.assertTrue(self.act(day, "tt-set", "07:00", field="allow_comment", value=True)[0])
        self.assertTrue(self.act(day, "tt-set", "07:00", field="commercial", value=True)[0])
        h = self.panel(day, "07:00")
        self.assertIn("Your brand", h)
        ok, said = self.act(day, "tt-set", "07:00", field="brand_content", value=True)
        self.assertTrue(ok)
        self.assertIn("can't be private", said)                             # Only me was cleared
        ch = B.read(self.acct, day)["slots"]["07:00"]["tiktok_post"]
        self.assertEqual((ch["privacy_level"], ch["allow_comment"], ch["brand_content"]), ("", True, True))
        h = self.panel(day, "07:00")
        self.assertIn("Paid partnership", h)
        self.assertIn('value="SELF_ONLY" disabled title="Branded content visibility cannot be set to private."', h)
        self.assertIn("Branded Content Policy</a> and", h)
        self.assertFalse(self.act(day, "tt-set", "07:00", field="privacy_level", value="SELF_ONLY")[0])

    def test_3_caption_edits_before_signing_only(self):
        day = date(2030, 2, 3)
        s = self.day_with(day, "0700", "words")
        ok, _ = self.act(day, "tt-caption", "07:00", text="New caption #gta6")
        self.assertTrue(ok)
        self.assertEqual(Path(s["copy"]["tiktok"]["path"]).read_text(), "New caption #gta6\n")
        self.assertFalse(self.act(day, "tt-caption", "07:00", text="x" * 2201)[0])
        S.approve(self.cfg, self.acct, day, ["07:00"], "Andrew", now=self.cfg.slot_time(day, "06:00"))
        self.assertFalse(self.act(day, "tt-caption", "07:00", text="after signing")[0])

    def test_4_post_now_waits_for_the_choices_then_sends(self):
        day = date(2030, 2, 4)
        self.day_with(day, "0700", "post-now")
        self.assertFalse(self.act(day, "tt-now", "07:00")[0])               # not signed yet
        S.approve(self.cfg, self.acct, day, ["07:00"], "Andrew", now=self.cfg.slot_time(day, "06:00"))
        h = self.panel(day, "07:00")
        self.assertIn('data-op="tt-now" data-acct="gaming" data-slot="07:00" disabled', h)
        self.assertIn("Choose who can see this video.", h)
        self.act(day, "tt-set", "07:00", field="privacy_level", value="PUBLIC_TO_EVERYONE")
        ok, said = self.act(day, "tt-now", "07:00")
        self.assertFalse(ok)                                                # the app isn't approved yet
        self.assertIn("Only me", said)
        self.act(day, "tt-set", "07:00", field="privacy_level", value="SELF_ONLY")
        self.assertNotIn(" disabled>Post to TikTok now", self.panel(day, "07:00"))
        ok, said = self.act(day, "tt-now", "07:00")
        self.assertTrue(ok, said)
        self.assertIn("few minutes", said)
        self.assertEqual(self.spawned, ["07:00"])

    def test_5_tiktok_now_records_what_happened(self):
        day = date(2030, 2, 5)
        self.day_with(day, "0700", "recorded")
        S.approve(self.cfg, self.acct, day, ["07:00"], "Andrew", now=self.cfg.slot_time(day, "06:00"))
        self.act(day, "tt-set", "07:00", field="privacy_level", value="SELF_ONLY")
        old = D.tiktok_post
        try:
            D.tiktok_post = lambda *a: (_ for _ in ()).throw(D.Refused("not posted: set it to private"))
            P.tiktok_now(self.cfg, self.acct, day, "07:00")
            rec = B.read(self.acct, day)["slots"]["07:00"]["outlets"]["tiktok"]
            self.assertIn("private", rec["last_error"])
            self.assertNotIn("waiting", rec)
            rows = R._plat_rows(self.cfg, self.acct, B.read(self.acct, day)["slots"]["07:00"], self.cfg.slot_time(day, "07:30"))
            self.assertIn(("warn", "TikTok"), [(r[0], r[1]) for r in rows])
            seen = []
            D.tiktok_post = lambda acct, clip, words, slot: seen.append(slot.get("_manual")) or {
                "post_id": "v_pub_1", "url": "https://www.tiktok.com/@gamingp", "privacy": "SELF_ONLY"}
            self.assertIn("on TikTok", P.tiktok_now(self.cfg, self.acct, day, "07:00"))
            self.assertEqual(seen, [True])
            self.assertIn("already", P.tiktok_now(self.cfg, self.acct, day, "07:00"))   # never twice
        finally:
            D.tiktok_post = old
        s = B.read(self.acct, day)["slots"]["07:00"]
        self.assertEqual(s["outlets"]["tiktok"]["post_id"], "v_pub_1")
        rows = R._plat_rows(self.cfg, self.acct, s, self.cfg.slot_time(day, "07:30"))
        self.assertIn(("hand", "TikTok", "private test post (Only me): post it by hand"), [r[:3] for r in rows])
        self.assertEqual(R._tt_panel(self.acct, s), "")                    # posted: the panel steps aside

    def test_6_the_post_carries_the_choices(self):
        clip = TMP / "c.mp4"
        clip.write_bytes(b"\0" * 2048)
        calls = []

        def fake_http(method, url, params=None, form=None, body=None, data=None, headers=None, timeout=120):
            calls.append((url.rsplit("/v2/", 1)[-1], body))
            if url.endswith("/video/init/"):
                return {"data": {"publish_id": "v_pub_9", "upload_url": "https://upload.example/x"}, "error": {"code": "ok"}}
            if url.endswith("/status/fetch/"):
                return {"data": {"status": "PUBLISH_COMPLETE"}}
            raise AssertionError(url)

        class Ok:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        old = (D._http, D.in_band, urllib.request.urlopen, D.net_guard_off if hasattr(D, "net_guard_off") else None)
        from clipfactory import net
        old_guard = net.guard
        D._http, D.in_band = fake_http, lambda *a: None
        urllib.request.urlopen = lambda req, timeout=0: Ok()
        net.guard = lambda what: None
        try:
            slot = {"clip": {"seconds": 2}, "_manual": True,
                    "tiktok_post": {"privacy_level": "SELF_ONLY", "allow_duet": True, "commercial": True,
                                    "brand_organic": True}}
            with self.assertRaises(D.Refused):                          # unapproved app, on a schedule: never
                D.tiktok_post(self.acct, clip, {"caption": "hi"}, dict(slot, _manual=False))
            res = D.tiktok_post(self.acct, clip, {"caption": "hi #gta6"}, slot)
        finally:
            D._http, D.in_band, urllib.request.urlopen = old[0], old[1], old[2]
            net.guard = old_guard
        init = [b for u, b in calls if u.endswith("init/")][0]["post_info"]
        self.assertEqual(init["privacy_level"], "SELF_ONLY")
        self.assertEqual((init["disable_comment"], init["disable_duet"], init["disable_stitch"]), (True, False, True))
        self.assertEqual((init["brand_organic_toggle"], init["brand_content_toggle"]), (True, False))
        self.assertEqual(init["title"], "hi #gta6")
        self.assertEqual((res["post_id"], res["privacy"], res["url"]), ("v_pub_9", "SELF_ONLY", "https://www.tiktok.com/@gamingp"))

    def test_7_a_scheduled_tiktok_post_waits_for_its_privacy_choice(self):
        day = date(2030, 2, 7)
        cfg = C.load(self.cfg_path)
        acct = cfg.account("gaming")
        acct.outlets["tiktok"] = {"mode": "api", "api_app_audited": True}
        deliver(acct.delivered_dirs(day)[0], "0700", "scheduled", 2.0)
        K.collect(cfg, acct, day)
        S.approve(cfg, acct, day, ["07:00"], "Andrew", now=cfg.slot_time(day, "06:00"))
        calls = []
        fake = {"tiktok": lambda a, clip, words, slot: calls.append(slot["slot"]) or {"post_id": "t1", "url": "u"}}
        P.post_due(cfg, now=cfg.slot_time(day, "07:05"), posters=fake)
        self.assertEqual(calls, [])
        rec = (B.read(acct, day)["slots"]["07:00"].get("outlets") or {}).get("tiktok") or {}
        self.assertFalse(rec.get("gave_up"))                                # waits, it doesn't give up
        rows = R._plat_rows(cfg, acct, B.read(acct, day)["slots"]["07:00"], cfg.slot_time(day, "07:05"))
        self.assertIn(("warn", "TikTok", "waits: choose who can see it (below)"), [r[:3] for r in rows])
        with B.edit(cfg, acct, day, create=False) as bd:
            bd["slots"]["07:00"]["tiktok_post"] = {"privacy_level": "PUBLIC_TO_EVERYONE"}
        P.post_due(cfg, now=cfg.slot_time(day, "07:25"), posters=fake)
        self.assertEqual(calls, ["07:00"])


if __name__ == "__main__":
    unittest.main()
