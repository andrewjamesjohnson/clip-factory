import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""The hook check's refusals are retried (gta-skill, 2026-10-17). run.py runs preflight a second time at 1.0x when
LENGTH or CUES refuse at the house tempo, and the maker read every preflight run of a pick: a LENGTH that the 1.0x
run fixed still blocked the retry of a PROMISE (or MUSIC) refusal in the last run. The maker now reads the LAST
preflight run only (makers._last_preflight). No skill is run; run.py is faked."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from clipfactory import board as B      # noqa: E402
from clipfactory import makers as M     # noqa: E402
from test_gta_maker_fixes import Base, cand, row   # noqa: E402

FIRST_LENGTH_THEN_PROMISE = (
    "\n== preflight\n   $ python3 preflight.py s\n"
    "    [FAIL] LENGTH   27.6s delivered: set \"speed\": 1.0 on the clip\n"
    "    [FAIL] PROMISE  'LUCIA IS CALLING FROM PRISON': the hook promises lucia, prison; the first 3s neither say nor show it\n"
    "   -- 4s, exit 1\n   LENGTH or CUES at the house tempo - the clip plays at 1.0x; preflight again\n"
    "\n== preflight (at 1.0x)\n   $ python3 preflight.py s\n"
    "    [ok  ] LENGTH   31.0s delivered\n"
    "    [FAIL] PROMISE  'LUCIA IS CALLING FROM PRISON': the hook promises lucia, prison; the first 3s neither say nor show it\n"
    "   -- 4s, exit 1\n\nSTOPPED at 'preflight (at 1.0x)' (exit 1); the whole output is in RUN.md")
STILL_SHORT_AT_1X = FIRST_LENGTH_THEN_PROMISE.replace("    [ok  ] LENGTH   31.0s delivered\n",
                                                      "    [FAIL] LENGTH   28.4s delivered\n")


class LastPreflight(Base):
    def test_the_last_preflight_run_is_read_alone(self):
        self.assertEqual(M.preflight_refusal(M._last_preflight(FIRST_LENGTH_THEN_PROMISE)), ["PROMISE"])
        self.assertEqual(sorted(M.preflight_refusal(M._last_preflight(STILL_SHORT_AT_1X))), ["LENGTH", "PROMISE"])
        plain = "    [FAIL] MUSIC x\nSTOPPED at 'preflight' (exit 1)"
        self.assertEqual(M._last_preflight(plain), plain)          # no header: the whole text, as before

    def test_a_promise_refusal_after_a_fixed_length_takes_the_next_candidate(self):
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        calls, lines = self.make(moments=(4000, cands), picks={1: (1, FIRST_LENGTH_THEN_PROMISE)}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2], lines)
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)

    def test_a_length_still_short_at_1x_takes_the_next_candidate(self):
        # LENGTH is retried since 10-18 (audit gta-yield): another span's silences differ
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        calls, _ = self.make(moments=(4000, cands), picks={1: (1, STILL_SHORT_AT_1X)}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2])
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)


if __name__ == "__main__":
    unittest.main()
