"""collect never takes an iCloud " 2" copy as the clip, the cover or the captions (audit FOLD-5). Throwaway folder."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-icloud-state-")

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import collect as K   # noqa: E402


class ICloudCopyTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="clipfactory-icloud-"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def touch(self, *names):
        for n in names:
            (self.d / n).write_bytes(b"x")

    def test_1_the_original_wins(self):
        self.touch("01-warsh 2.mp4", "01-warsh.mp4", "instagram.mp4")
        self.assertEqual(K.main_clip(self.d).name, "01-warsh.mp4")

    def test_2_a_copy_only_when_it_is_all_there_is(self):
        self.touch("01-warsh 2.mp4", "instagram.mp4")
        self.assertEqual(K.main_clip(self.d).name, "01-warsh 2.mp4")

    def test_3_cover_and_captions(self):
        self.touch("01-warsh 2.jpg", "01-warsh.jpg", "01-warsh 3.srt", "01-warsh.srt")
        self.assertEqual(K._originals_first(self.d.glob("*.jpg"))[0].name, "01-warsh.jpg")
        self.assertEqual(K._originals_first(self.d.glob("*.srt"))[0].name, "01-warsh.srt")

    def test_4_a_name_with_a_number_inside_is_not_a_copy(self):
        self.touch("01-stunt-29-beat-it.mp4", "02-top 10.mp4")
        self.assertEqual([p.name for p in K._originals_first(self.d.glob("*.mp4"))], ["01-stunt-29-beat-it.mp4"])


if __name__ == "__main__":
    unittest.main()
