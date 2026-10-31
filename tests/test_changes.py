"""The change politics (the owner, 2026-10-30, the loops thesis item 2). Throwaway folders; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-changes-state-"))

import json
import random
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import changes as CH   # noqa: E402
from clipfactory import config as C     # noqa: E402


class ChangesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-changes-"))
        (self.tmp / "config").mkdir()
        shutil.copy(ROOT / "config" / "accounts.json", self.tmp / "config" / "accounts.json")
        self.cfg = C.load(self.tmp / "config" / "accounts.json")
        self.tz = self.cfg.now().tzinfo
        self.live = datetime(2031, 6, 10, 12, 0, tzinfo=self.tz)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def posts(self, before: float, after: float, n: int = 15, outlet: str = "facebook_reels"):
        rnd = random.Random(7)
        out = []
        for i in range(n):
            for shift, level in ((-1, before), (1, after)):
                t = self.live + timedelta(hours=shift * (6 + i * 9))
                out.append({"outlet": outlet, "went": t.isoformat(), "rel_24h": level + rnd.uniform(-0.15, 0.15)})
        return out

    def row(self, **kw):
        r = {"id": "hook-fixes", "what": "Hook fixes", "account": "politics", "live": "2031-06-10 12:00",
             "metric": "views", "window_days": 7}
        r.update(kw)
        return r

    def test_verdicts(self):
        later = self.live + timedelta(days=8)
        self.assertEqual(CH.judge(self.cfg, self.row(), self.posts(0.0, 0.7), later, [])["verdict"], "KEEP")
        self.assertEqual(CH.judge(self.cfg, self.row(), self.posts(0.0, -0.7), later, [])["verdict"], "ROLL BACK?")
        self.assertEqual(CH.judge(self.cfg, self.row(), self.posts(0.0, 0.0), later, [])["verdict"], "NO EFFECT")
        j = CH.judge(self.cfg, self.row(), self.posts(0.0, 0.7), self.live + timedelta(days=2), [])
        self.assertEqual((j["verdict"], j["due"]), ("WAIT", False))
        j = CH.judge(self.cfg, self.row(), self.posts(0.0, 0.7, n=5), later, [])
        self.assertEqual((j["verdict"], j["due"]), ("WAIT", True))
        self.assertIn("too few", j["words"])
        other = CH.judge(self.cfg, self.row(outlets=["youtube_shorts"]), self.posts(0.0, 0.7), later, [])
        self.assertEqual((other["n_before"], other["n_after"]), (0, 0))
        self.assertEqual(CH.judge(self.cfg, self.row(metric="manual", judge="fb_cap_test.py"), [], later, [])["verdict"],
                         "BY HAND")

    def test_tangled_and_add(self):
        self.assertIn("added hook-fixes", CH.add(self.cfg, self.row()))
        msg = CH.add(self.cfg, self.row(id="covers", what="Covers", live="2031-06-12 09:00"))
        self.assertIn("tangled with hook-fixes", msg)
        CH.add(self.cfg, self.row(id="gta-cap", account="gaming", live="2031-06-11"))
        rows = CH.load(self.cfg)
        self.assertEqual([r["id"] for r in rows], ["hook-fixes", "covers", "gta-cap"])
        self.assertEqual(CH.tangled(self.cfg, rows[0], rows), ["covers"])
        self.assertEqual(CH.tangled(self.cfg, rows[2], rows), [])
        for bad in (dict(id="Bad Id"), dict(id="covers"), dict(id="x2", account="nobody"),
                    dict(id="x3", outlets=["myspace"]), dict(id="x4", live="tomorrow"), dict(id="x5", what=""),
                    dict(id="x6", metric="likes"), dict(id="x7", window_days=0)):
            with self.assertRaises(SystemExit, msg=str(bad)):
                CH.add(self.cfg, self.row(**bad))
        self.assertEqual(len(CH.load(self.cfg)), 3)
        self.assertTrue(json.loads((self.tmp / "config" / "changes.json").read_text()))

    def test_the_politics_rows_in_the_repo_read(self):
        cfg = C.load(ROOT / "config" / "accounts.json")
        rows = CH.load(cfg)
        self.assertTrue(rows)
        for r in rows:
            CH.check(cfg, r, [x for x in rows if x is not r])


if __name__ == "__main__":
    unittest.main()
