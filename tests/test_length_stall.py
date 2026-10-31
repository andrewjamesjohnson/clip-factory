"""A clip length this Mac cannot read is one of the tries, not a refusal (2026-10-30, 06:39-07:15: ffprobe hung on
three accounts' clips while the poster could read their bytes; in_band refused, every slot gave up at once, and GTA
Gaming's 07:00 posted nowhere until Try again at 15:18). Throwaway folders and fake platforms only (clipfactory/net.py)."""
from __future__ import annotations

import os as _os
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-stall-state-"))

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

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
RUN = subprocess.run

from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def ffprobe(stalls: list, how: str = "timeout"):
    """subprocess.run with in_band's ffprobe stalling while `stalls` holds a True: given no answer (timeout), not
    started (gone) or printing no length (empty). Every other call runs as it is."""
    def run(args, *a, **k):
        if not (args and args[0] == "ffprobe" and "format=duration" in args):
            return RUN(args, *a, **k)
        if not (stalls and stalls.pop(0)):
            return RUN(args, *a, **k)
        if how == "gone":
            raise FileNotFoundError(2, "No such file or directory", "ffprobe")
        if how == "empty":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="moov atom not found\n")
        raise subprocess.TimeoutExpired(args, k.get("timeout"))
    return run


class LengthStallTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-stall-"))
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
        self.posts = []

    def ig(self):
        """Instagram as the real ig_post starts: the length wall first, then the post."""
        def post(acct, path, words, slot):
            D.in_band(acct, path, "instagram_reels")
            self.posts.append(path.name)
            return {"post_id": "M1", "url": "https://www.instagram.com/reel/M1/"}
        return {"instagram_reels": post}

    def rec(self) -> dict:
        return B.read(self.acct, DAY)["slots"]["07:00"]["outlets"]["instagram_reels"]

    def run_until(self, stalls: list, minutes: int, step: int = 5) -> list:
        """post-due every `step` minutes from 07:00 for `minutes`; the minutes (after 07:00) at which a try was made."""
        at, gave = [], []
        with mock.patch.object(subprocess, "run", ffprobe(stalls)):
            for m in range(0, minutes + 1, step):
                before = len(stalls)
                P.post_due(self.cfg, now=self.cfg.slot_time(DAY, "07:00") + timedelta(minutes=m), posters=self.ig())
                if len(stalls) < before:
                    at.append(m)
                r = B.read(self.acct, DAY)["slots"]["07:00"].get("outlets", {}).get("instagram_reels", {})
                gave.append(bool(r.get("gave_up")))
                if self.posts:
                    break
        self.gave = gave
        return at

    def test_1_a_stalled_length_is_a_try_and_the_clip_posts_once_it_reads(self):
        at = self.run_until([True, True, True, False], minutes=120)
        self.assertEqual(at, [0, 5, 15, 30])                            # UPLOAD_GAPS apart: 5, 10, 15
        self.assertEqual(self.posts, ["01-the-exchange.mp4"])
        self.assertFalse(any(self.gave[:-1]), self.gave)                  # never gave up on the way
        r = self.rec()
        self.assertEqual(r["post_id"], "M1")
        for k in ("tries", "last_error", "tried_at", "upload_fails", "gave_up", "why"):
            self.assertNotIn(k, r)

    def test_2_a_length_that_never_reads_gets_ten_tries_then_a_person(self):
        at = self.run_until([True] * 99, minutes=600)
        self.assertEqual(len(at), 10, at)
        self.assertEqual([b - a for a, b in zip(at, at[1:])], list(P.UPLOAD_GAPS))
        r = self.rec()
        self.assertTrue(r["gave_up"])
        self.assertEqual(r["tries"], 10)
        self.assertIn("failed 10 times: could not read the length of 01-the-exchange.mp4 (ffprobe gave no answer in "
                      f"{D.LENGTH_WAIT}s)", r["why"])
        self.assertNotIn("upload_fails", r)                               # not Meta's upload service
        self.assertEqual(self.posts, [])

    def test_3_each_way_ffprobe_fails_is_unreadable_and_the_band_still_refuses(self):
        f = self.master
        for how, words in (("timeout", f"ffprobe gave no answer in {D.LENGTH_WAIT}s"), ("gone", "ffprobe did not run"),
                           ("empty", "ffprobe printed no length: moov atom not found")):
            with mock.patch.object(subprocess, "run", ffprobe([True], how)), \
                    self.assertRaises(D.Unreadable) as cm:
                D.in_band(self.acct, f, "instagram_reels")
            self.assertNotIsInstance(cm.exception, D.Refused, how)       # one of the tries, not a give-up
            self.assertIn(f"could not read the length of {f.name} ({words}", str(cm.exception))
        bin_dir = self.tmp / "bin"                                        # a real ffprobe that hangs: killed, said so
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "ffprobe").write_text("#!/bin/sh\nexec sleep 5\n")
        (bin_dir / "ffprobe").chmod(0o755)
        with mock.patch.object(D, "LENGTH_WAIT", 1), \
                mock.patch.dict(os.environ, {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}), \
                self.assertRaises(D.Unreadable) as cm:
            D.in_band(self.acct, f, "instagram_reels")
        self.assertIn("ffprobe gave no answer in 1s", str(cm.exception))
        long = self.tmp / "long.mp4"                                      # a length it reads still meets the wall
        clip(long, 5.0)
        with self.assertRaises(D.Refused) as cm:
            D.in_band(self.acct, long, "instagram_reels")
        self.assertNotIsInstance(cm.exception, D.Unreadable)
        self.assertIn("outside", str(cm.exception))

    def test_4_the_dry_run_and_the_card_say_it_plainly(self):
        now = self.cfg.slot_time(DAY, "07:00")
        with mock.patch.object(subprocess, "run", ffprobe([True])):
            lines = P.post_due(self.cfg, now=now, posters=self.ig(), dry_run=True)
        said = "\n".join(lines) if isinstance(lines, list) else str(lines)
        self.assertIn("would NOT post now: could not read the length of 01-the-exchange.mp4", said)
        self.assertNotIn("instagram_reels", B.read(self.acct, DAY)["slots"]["07:00"].get("outlets", {}))
        self.run_until([True], minutes=0)
        s = B.read(self.acct, DAY)["slots"]["07:00"]
        ig = next(r for r in R._plat_rows(self.cfg, self.acct, s, self.cfg.slot_time(DAY, "07:01")) if r[1] == "Instagram")
        self.assertEqual(ig[0], "warn")
        self.assertIn("try 1 of 10 failed, tries again about 07:05: could not read the length of 01-the-exchange.mp4",
                      ig[2])


if __name__ == "__main__":
    unittest.main()
