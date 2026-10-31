import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The automatic sign-off meets the social layer (merge of 2026-10-20): a ready clip with an extras file (its first
# comment) signs itself 30 minutes before its slot through Approve's own hash, so the first comment is covered by the
# factory's signature exactly as by Andrew's; after the post, the comment goes out through outward only when its
# first_comment switch is on (practice makes no call; off queues nothing); and the signer on the board, on the queued
# comment and in the learner's verdict row is "Clip Factory (auto)", never Andrew. Fake platforms, throwaway folders.

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import Clock, FakeGraph, FakeYouTube, cfg_in_tmp, patch_http, slot_ready, write_tokens  # noqa: E402

from clipfactory import autosign as A           # noqa: E402
from clipfactory import board as B              # noqa: E402
from clipfactory import capabilities as CAP     # noqa: E402
from clipfactory import extras as EX            # noqa: E402
from clipfactory import firstcomment as FC      # noqa: E402
from clipfactory import outward as OW           # noqa: E402
from clipfactory import poster as P             # noqa: E402
from clipfactory import store                   # noqa: E402
from clipfactory import switches as SW          # noqa: E402
from clipfactory import verdicts as VD          # noqa: E402

DAY = date(2031, 7, 8)
K = "10:30"
WORDS = "Full video from MrBossFTW: https://www.youtube.com/watch?v=uSQfYvi-NfY&t=190s"
META = ["pages_manage_engagement", "pages_read_engagement", "pages_read_user_content", "instagram_basic",
        "instagram_manage_comments", "instagram_content_publish", "pages_manage_posts"]


class AutoSignedExtrasTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.assertTrue(A.on(self.cfg, self.gta), "config/accounts.json has the automatic sign-off off")
        self.clock = Clock(self.cfg.slot_time(DAY, "09:50")).patch(self, self.cfg)
        FC.RNG.seed(7)
        OW.RNG.seed(7)
        self.addCleanup(SW._SEEN.clear)
        write_tokens(self.gta)
        CAP.save_scopes(self.gta, "meta", list(META), "connect")
        CAP.save_scopes(self.gta, "youtube", ["youtube.upload", "youtube.readonly"], "connect")

    def mode(self, value: str) -> None:
        p = SW.file_path(self.gta)
        raw = store.read(p, {}) or {}
        raw.setdefault("modes", {})["first_comment.facebook_reels"] = value
        store.write(p, raw)
        SW._SEEN.clear()

    def slot(self) -> dict:
        return B.read(self.gta, DAY)["slots"][K]

    def ready_with_comment(self) -> None:
        slot_ready(self, self.gta, DAY, K)
        with EX.edit(self.cfg, self.gta, DAY, K, "collect", "first comment") as x:
            x["first_comment"] = {"facebook_reels": {"text": WORDS, "from": "default"}}
        self.assertTrue((self.slot().get("extras") or {}).get("path"), "no extras file: the test proves nothing")

    def auto_sign(self) -> dict:
        self.assertEqual(A.run(self.cfg, self.cfg.slot_time(DAY, "09:59")), [])     # not yet: 31 minutes out
        self.assertEqual(self.slot()["status"], B.READY)
        out = A.run(self.cfg, self.clock.at("10:01"))
        s = self.slot()
        self.assertIn(s["status"], B.SIGNED, out)
        return s

    def posted(self) -> dict:
        """The poster's Facebook record for the slot, as a confirmed post leaves it (the signature untouched)."""
        with B.edit(self.cfg, self.gta, DAY) as bd:
            s = bd["slots"][K]
            at = datetime.fromisoformat(str(s["publish_at"])).astimezone(timezone.utc).replace(microsecond=0)
            pid = f"1{DAY:%y%m%d}103000"
            rec = {"post_id": pid, "platform_id": pid, "url": f"https://www.facebook.com/reel/{pid}",
                   "published_at": at.isoformat(), "at": at.isoformat(), "provider": "direct"}
            rec.update(P._shas(s, "facebook_reels"))
            s.setdefault("outlets", {})["facebook_reels"] = rec
        s = self.slot()
        self.clock.at("10:31")
        return s

    def hook(self):
        s = self.slot()
        return FC.on_posted(self.cfg, self.gta, DAY, s, "facebook_reels", s["outlets"]["facebook_reels"], self.clock())

    def items(self):
        return list(OW._items(self.gta.slug).values())

    def assert_signed_by_the_factory_with_its_extras(self, s: dict) -> None:
        so = s["signoff"]
        self.assertEqual(so["by"], A.BY)
        self.assertTrue(so.get("auto"))
        self.assertNotIn("Andrew", so["by"])
        self.assertEqual(so["hash"], B.content_hash(s))                 # Approve's own hash ...
        self.assertEqual(B.verify(s), [])
        x = Path(s["extras"]["path"])                                    # ... which covers the first comment
        old = x.read_text()
        x.write_text(old.replace("MrBossFTW", "Someone"))
        self.assertTrue(B.verify(s), "an edit to the extras file after the automatic sign-off must void it")
        x.write_text(old)
        self.assertEqual(B.verify(s), [])
        rows = [r for r in VD.rows(self.gta) if r.get("slot") == K]
        self.assertEqual([(r["verdict"], r["by"]) for r in rows], [("approve", A.BY)])
        self.assertIn("signed automatically", rows[0]["note"])
        self.assertFalse(any("Andrew" in str(r.get("by")) for r in VD.rows(self.gta)))

    def test_on_the_auto_signed_first_comment_goes_out(self):
        self.mode("on")
        g = FakeGraph().on("POST", r"/\d+/comments$", {"id": "c-auto"})
        patch_http(self, graph=g, yt=FakeYouTube())
        self.ready_with_comment()
        s = self.auto_sign()
        self.assert_signed_by_the_factory_with_its_extras(s)
        self.posted()
        self.assertIn("first comment queued", self.hook() or "")
        (it,) = self.items()
        self.assertEqual(it["signed"]["by"], A.BY)
        self.assertEqual(it["signed"]["hash"], s["signoff"]["hash"])
        self.assertEqual(it["signed"]["extras_sha"], s["extras"]["sha256"])
        self.clock.at("10:40")
        res = OW.send(self.cfg, self.gta, it["id"], self.clock())
        self.assertEqual(res["state"], "done", res)
        (call,) = g.calls
        self.assertEqual(call["form"]["message"], WORDS)
        self.assertEqual(FC.marker(self.slot()["outlets"]["facebook_reels"])["state"], "done")

    def test_practice_the_auto_signed_first_comment_makes_no_call(self):
        self.mode("practice")
        g = FakeGraph()                                    # no routes: any call fails the test
        patch_http(self, graph=g, yt=FakeYouTube())
        self.ready_with_comment()
        self.assert_signed_by_the_factory_with_its_extras(self.auto_sign())
        self.posted()
        self.hook()
        (it,) = self.items()
        self.assertEqual(it["signed"]["by"], A.BY)
        self.clock.at("10:40")
        res = OW.send(self.cfg, self.gta, it["id"], self.clock())
        self.assertEqual(res["state"], "practice")
        self.assertEqual(g.calls, [])
        rows = list(OW.rows(self.cfg, self.gta, kinds=("first_comment",), states=("practice",)))
        self.assertEqual([r["text"] for r in rows], [WORDS])

    def test_off_the_auto_signed_clip_queues_no_first_comment(self):
        g = FakeGraph()
        patch_http(self, graph=g, yt=FakeYouTube())
        self.ready_with_comment()                          # the extras file is there, the switch is not
        self.assert_signed_by_the_factory_with_its_extras(self.auto_sign())
        self.posted()
        self.assertIsNone(self.hook())
        self.assertEqual(self.items(), [])
        self.assertEqual(FC.sweep(self.cfg, now=self.clock()), [])
        self.assertEqual(g.calls, [])


if __name__ == "__main__":
    unittest.main()
