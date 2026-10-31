"""THE CUT MATCHES ITS CLIP (2026-10-31): a name plate the clip's own render dropped too does not refuse its Instagram
or YouTube cut (10-31 11:50 Dunleavy and 20:40 El-Sayed went out with "cards": [] and their cuts were refused); a plate
the clip shows that the cut lost still refuses, and so does a cut whose clip's plates cannot be read."""
from __future__ import annotations

import os as _os
import tempfile as _tempfile

_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-plates-state-"))

import json  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import igcut as I  # noqa: E402


class Plates(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="plates-"))
        (self.d / "out").mkdir()
        (self.d / "job" / "clips").mkdir(parents=True)
        (self.d / "out" / "01-x-ig.cards.json").write_text(json.dumps({"cards": []}))
        self.clipd = {"slug": "x-ig", "cards": [{"who": "dunleavy", "at": 7.5}]}

    def _main(self, cards):
        (self.d / "job" / "clips" / "01-x.cards.json").write_text(json.dumps({"cards": cards}))
        return I._main_plates(self.d / "job", "x")

    def test_the_clip_went_out_without_it_too(self):
        look = I._plates(self.clipd, self.d / "out", self.d / "log", self._main([]))
        self.assertTrue(any("went out without one" in x for x in look), look)

    def test_a_plate_the_clip_shows_still_refuses(self):
        with self.assertRaises(I.CutError):
            I._plates(self.clipd, self.d / "out", self.d / "log", self._main([{"who": "dunleavy"}]))

    def test_a_clip_whose_plates_cannot_be_read_refuses_as_before(self):
        with self.assertRaises(I.CutError):
            I._plates(self.clipd, self.d / "out", self.d / "log", I._main_plates(self.d / "nojob", "x"))


if __name__ == "__main__":
    unittest.main()
