"""Meta's upload service failing an Instagram upload on its side (rupload ProcessingFailedError, 2026-10-16 to 10-20:
the same file failed in spells and went through on a later try): more tries, further apart, said plainly on the card,
and the container's own words on the board. Throwaway folders and fake platforms only (clipfactory/net.py)."""
from __future__ import annotations

import os as _os
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-igretry-state-"))

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import review as R     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

DAY = date(2031, 5, 6)
META_FAIL = ('HTTP 400 from rupload.facebook.com: {"debug_info":{"retriable":false,"type":"ProcessingFailedError",'
             '"message":"Request processing failed"}}')


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class UploadRetryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-igretry-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw.pop("auto_sign", None)
        for a in raw["accounts"].values():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "export"}, "facebook_reels": {"mode": "export"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.master = cls.tmp / "master.mp4"
        clip(cls.master, 2.0)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens

    def setUp(self):
        for d in ("state", "drafts", "delivered", "keys"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("politics")
        (O.TOKENS / self.acct.slug).mkdir(parents=True, exist_ok=True)
        (O.TOKENS / self.acct.slug / "meta.json").write_text("{}")      # signed in (a file only: nothing leaves)
        d = self.acct.delivered_dirs(DAY)[0] / "0700 the-exchange"
        d.mkdir(parents=True)
        shutil.copy(self.master, d / "01-the-exchange.mp4")
        for p in ("youtube", "tiktok_reach", "facebook", "instagram"):
            (d / f"{p}.txt").write_text("TITLE\nThe exchange\n\nCAPTION\nWords.\n")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.tries = []

    def ig(self, fails: int, err: str = META_FAIL):
        """Instagram that fails the first `fails` tries with `err`, then posts."""
        def post(acct, path, words, slot):
            self.tries.append(len(self.tries) + 1)
            if len(self.tries) <= fails:
                raise D.PlatformError(err)
            return {"post_id": "M1", "url": "https://www.instagram.com/reel/M1/"}
        return {"instagram_reels": post}

    def rec(self) -> dict:
        return B.read(self.acct, DAY)["slots"]["07:00"]["outlets"]["instagram_reels"]

    def run_until(self, posters, minutes: int, step: int = 1) -> list:
        """post-due every `step` minutes from 07:00 for `minutes`; the minutes (after 07:00) at which a try was made."""
        at, seen = [], 0
        for m in range(0, minutes + 1, step):
            P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:00") + timedelta(minutes=m), posters=posters)
            if len(self.tries) > seen:
                seen = len(self.tries)
                at.append(m)
        return at

    def test_1_meta_upload_failures_get_ten_tries_further_apart(self):
        at = self.run_until(self.ig(fails=99), minutes=600, step=5)
        self.assertEqual(len(at), 10, at)                                   # not three
        self.assertEqual([b - a for a, b in zip(at, at[1:])], list(P.UPLOAD_GAPS))
        r = self.rec()
        self.assertTrue(r["gave_up"])
        self.assertEqual(r["tries"], 10)
        self.assertIn("failed 10 times", r["why"])

    def test_2_the_same_file_posts_on_a_later_try_and_the_record_is_clean(self):
        at = self.run_until(self.ig(fails=4), minutes=120)
        self.assertEqual(len(at), 5, at)
        r = self.rec()
        self.assertEqual(r["post_id"], "M1")
        for k in ("tries", "last_error", "tried_at", "upload_fails", "gave_up"):
            self.assertNotIn(k, r)

    def test_3_any_other_error_keeps_three_tries_five_minutes_apart(self):
        at = self.run_until(self.ig(fails=99, err="HTTP 500 from graph.facebook.com: An unknown error occurred"),
                            minutes=60)
        self.assertEqual(at, [0, 5, 10])
        self.assertTrue(self.rec()["gave_up"])

    def test_4_one_meta_upload_failure_among_others_keeps_the_longer_budget(self):
        errs = ["<urlopen error [Errno 8] nodename nor servname provided, or not known>", META_FAIL,
                "<urlopen error [Errno 8] nodename nor servname provided, or not known>"]

        def post(acct, path, words, slot):
            self.tries.append(len(self.tries) + 1)
            raise D.PlatformError(errs[min(len(self.tries), len(errs)) - 1])
        self.run_until({"instagram_reels": post}, minutes=12)
        r = self.rec()
        self.assertEqual(r["tries"], 3)
        self.assertNotIn("gave_up", r)                  # a lost connection after Meta's upload error: still trying
        self.assertEqual(P.max_tries(r), len(P.UPLOAD_GAPS) + 1)
        self.assertEqual(P.retry_after(r), P.RETRY_AFTER)     # the wait follows the last error: a lost connection

    def test_5_the_card_says_it_is_meta_and_when_it_tries_again(self):
        self.run_until(self.ig(fails=99), minutes=0)
        s = B.read(self.acct, DAY)["slots"]["07:00"]
        rows = R._plat_rows(self.cfg, self.acct, s, self.cfg.slot_time(DAY, "07:01"))
        ig = next(r for r in rows if r[1] == "Instagram")
        self.assertEqual(ig[0], "warn")
        self.assertIn("try 1 of 10 failed, tries again about 07:05", ig[2])
        self.assertIn("Meta's upload service failed it on their side", ig[2])
        self.assertNotIn("debug_info", ig[2])

    def test_6_after_ten_the_card_offers_try_again_and_that_gives_ten_more(self):
        self.run_until(self.ig(fails=99), minutes=600, step=5)
        s = B.read(self.acct, DAY)["slots"]["07:00"]
        now = self.cfg.slot_time(DAY, "17:30")
        ig = next(r for r in R._plat_rows(self.cfg, self.acct, s, now) if r[1] == "Instagram")
        self.assertIn("Meta's upload service failed it 10 times", ig[2])
        self.assertIn('data-op="retry"', R._card(self.cfg, self.acct, DAY, s, now)["bottom"])
        S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertNotIn("instagram_reels", B.read(self.acct, DAY)["slots"]["07:00"].get("outlets", {}))
        n = len(self.tries)
        P.post_due(self.cfg, now=now, posters=self.ig(fails=99))
        self.assertEqual(len(self.tries), n + 1)
        self.assertEqual(self.rec()["tries"], 1)

    def test_7_the_board_gets_what_the_container_says(self):
        calls = []

        def http(method, url, params=None, form=None, body=None, data=None, headers=None, timeout=0):
            calls.append((method, url.split("?")[0]))
            if url.endswith("/media") and method == "POST":
                return {"id": "C9", "uri": "https://rupload.facebook.com/ig-api-upload/v25.0/C9"}
            if "ig-api-upload" in url:
                raise D.PlatformError(META_FAIL)
            if url.endswith("/C9") and method == "GET":
                return {"status_code": "IN_PROGRESS", "video_status": {"uploading_phase": {
                    "status": "error", "bytes_transferred": 0, "errors": [
                        {"code": 1363008, "message": "OIL Error[FILE_NOT_FOUND: 6]: File not found."}]}}}
            raise AssertionError(f"unexpected {method} {url}")
        old = (D._http, D._load, D.in_band)
        D._http, D._load = http, (lambda acct, name, how: {"page_token": "t", "ig_user_id": "IG1"})
        D.in_band = lambda *a, **k: None
        try:
            with self.assertRaises(D.PlatformError) as cm:
                D.ig_post(self.acct, self.master, {"caption": "Words."}, {"slot": "07:00"})
        finally:
            D._http, D._load, D.in_band = old
        self.assertIn("ProcessingFailedError", str(cm.exception))          # still Meta's upload error to the poster
        self.assertIn("(container C9: upload error, 0 bytes, OIL Error[FILE_NOT_FOUND: 6]", str(cm.exception))
        self.assertTrue(P.upload_failed(str(cm.exception)))
        self.assertNotIn(("POST", f"{D.GRAPH}/IG1/media_publish"), calls)     # a failed upload is never published


if __name__ == "__main__":
    unittest.main()
