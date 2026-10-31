"""Replay real nights (the owner, 2026-10-31, the loops thesis item 7). A stand-in engine; throwaway folders; no
network; nothing real is read."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-replay-state-"))

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C      # noqa: E402
from clipfactory import replay as RP     # noqa: E402

FAKE_PREFLIGHT = """import json, os, pathlib
w = pathlib.Path(os.environ["CLIP_WORK"])
p = json.loads((w / "project.json").read_text())
assert p["out_dir"] == str(w), "a replay must never point out_dir at a real folder"
hook = os.environ.get("FAKE_HOOK", "ok  ")
print("    [ok  ] SAFETY   13 shipped fields clear")
print(f"    [{hook}] HOOK     6 words")
print("ALL CLEAR - render." if hook.startswith("ok") else "STOPPED: refused at HOOK")
"""


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-replay-"))
        self.old = (C.STATE, RP.ENGINE, RP.PY)
        C.STATE = self.tmp / "state"
        RP.ENGINE = self.tmp / "engine"
        RP.ENGINE.mkdir()
        (RP.ENGINE / "preflight.py").write_text(FAKE_PREFLIGHT)
        RP.PY = sys.executable
        self.job = self.tmp / "Jobs" / "Fox10 Daytona"
        (self.job / "clips").mkdir(parents=True)
        master = self.tmp / "clipwork" / "master.mp4"
        master.parent.mkdir()
        clip(master, 1.0)
        clip(self.job / "clips" / "01-a.mp4", 1.0)
        (self.job / "clips" / "01-a.coldopen.json").write_text(json.dumps({"text": "Abdul is a jihadist."}))
        (self.job / "clips" / "01-a.cards.json").write_text(json.dumps({"cards": [{"name": "Donald Trump"}]}))
        (self.job / "slate.json").write_text(json.dumps({"clips": [{"rank": 1, "slug": "a"}]}))
        (self.job / "project.json").write_text(json.dumps({"source": str(master), "out_dir": str(self.job)}))

    def tearDown(self):
        C.STATE, RP.ENGINE, RP.PY = self.old
        _os.environ.pop("FAKE_HOOK", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_freeze_then_run_the_same_then_a_broken_hook(self):
        g = RP.freeze_job(self.job, "2026-10-30")
        dst = RP.root() / "golden" / "2026-10-30" / "Fox10-Daytona"
        self.assertEqual(g["preflight"]["VERDICT"], "ALL CLEAR - render.")
        self.assertEqual(g["made"]["01-a.mp4"]["cards"], ["Donald Trump"])
        self.assertEqual(json.loads((self.job / "project.json").read_text())["out_dir"], str(self.job))  # untouched
        self.assertEqual(json.loads((dst / "project.json").read_text())["out_dir"], str(dst))
        self.assertIn("1 the same", RP.run()[0])
        _os.environ["FAKE_HOOK"] = "FAIL"
        out = "\n".join(RP.run())
        self.assertIn("with a new FAIL", out.splitlines()[0])
        self.assertIn("NEW FAIL 2026-10-30 Fox10 Daytona: HOOK", out)

    def test_made_and_compare_made(self):
        m = RP.made(self.job)
        self.assertEqual(m["01-a.mp4"]["cold_open"], "Abdul is a jihadist.")
        lost = {"01-a.mp4": dict(m["01-a.mp4"], cold_open=None, seconds=m["01-a.mp4"]["seconds"] + 2)}
        diffs = RP.compare_made({"made": m}, lost)
        self.assertTrue(any("cold open lost" in d for d in diffs))
        self.assertTrue(any(" s -> " in d for d in diffs))


if __name__ == "__main__":
    unittest.main()
