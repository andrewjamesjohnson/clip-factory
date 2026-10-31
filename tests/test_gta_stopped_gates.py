import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""run.py's STOPPED line names the gates preflight refused (gta-skill2, 2026-10-18), and the maker reads it first.
A step echoes only its last 24 lines, so a clip's own [FAIL] lines scrolled away above the set's and the maker read
no gate: three of the 10-17 night's ten refusals were never retried. No skill is run; run.py is faked."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from clipfactory import board as B      # noqa: E402
from clipfactory import makers as M     # noqa: E402
from test_gta_maker_fixes import Base, cand, row   # noqa: E402

# a pick refused on MUSIC whose [FAIL] line scrolled out of the echo: only the set's lines and the STOPPED line show
SCROLLED = ("\n== preflight\n   $ python3 preflight.py s\n"
            + "".join(f"       [ok  ] X{i:02d}      fine\n" for i in range(20))
            + "   THE SET\n       [ok  ] SET      no picture twice\n   NOT READY: s. Fix the slate, re-run preflight, then render.\n"
            "   -- 61s, exit 1\n\nSTOPPED at 'preflight' (exit 1; refused: MUSIC); the whole output is in RUN.md")
TOOL = ("\n== preflight\n   $ python3 preflight.py s\n   NOT READY: the tool itself. Nothing below was checked.\n"
        "   -- 2s, exit 1\n\nSTOPPED at 'preflight' (exit 1; refused: TOOL); the whole output is in RUN.md")


class StoppedLine(Base):
    def test_the_stopped_line_is_read(self):
        self.assertEqual(M.stopped_gates("STOPPED at 'preflight' (exit 1; refused: SET, MUSIC); the whole output is in R"),
                         ["MUSIC", "SET"])
        self.assertEqual(M.stopped_gates("STOPPED at 'preflight (at 1.0x)' (exit 1; refused: LENGTH, PROMISE); x"),
                         ["LENGTH", "PROMISE"])
        self.assertIsNone(M.stopped_gates("STOPPED at 'preflight' (exit 1; no [FAIL] line in its output); x"))
        self.assertIsNone(M.stopped_gates("STOPPED at 'preflight' (exit 1); the whole output is in RUN.md"))   # old run.py
        self.assertIsNone(M.stopped_gates("STOPPED at 'the render (build_all.py)' (exit 1); x"))
        self.assertIsNone(M.stopped_gates(""))

    def test_the_line_comes_first_and_the_old_reading_stays(self):
        # the STOPPED line names more than the echo shows: the line wins (it read the whole output)
        self.assertEqual(M.preflight_refusal("    [FAIL] SET x\nSTOPPED at 'preflight' (exit 1; refused: MUSIC, SET); y"),
                         ["MUSIC", "SET"])
        self.assertEqual(M.preflight_refusal(SCROLLED), ["MUSIC"])
        # no gate named: the old reading, exactly as before
        self.assertEqual(M.preflight_refusal("[FAIL] MUSIC x\nSTOPPED at 'preflight' (exit 1)"), ["MUSIC"])
        self.assertEqual(M.preflight_refusal("[FAIL] MUSIC x\nSTOPPED at 'preflight' (exit 1; no [FAIL] line in its output)"),
                         ["MUSIC"])
        self.assertIsNone(M.preflight_refusal("[FAIL] MUSIC x\nSTOPPED at 'the slate' (exit 1)"))
        self.assertEqual(M.preflight_refusal("[ BAD  ] DEADAIR 1.6s\nSTOPPED at the grade: the delivered clip has dead "
                                             "air (the DEADAIR line above); x"), ["DEADAIR at the grade"])
        # the last preflight run only (wave 1's _last_preflight), with the line in it
        two = ("\n== preflight\n    [FAIL] LENGTH 27.6s\n   LENGTH or CUES at the house tempo - preflight again\n"
               "\n== preflight (at 1.0x)\n    [ok  ] LENGTH 31.0s\n\n"
               "STOPPED at 'preflight (at 1.0x)' (exit 1; refused: PROMISE); RUN.md")
        self.assertEqual(M.preflight_refusal(M._last_preflight(two)), ["PROMISE"])

    def test_a_refusal_the_echo_lost_takes_the_next_candidate(self):
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        calls, lines = self.make(moments=(4000, cands), picks={1: (1, SCROLLED)}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2], lines)
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)
        self.assertTrue(any("candidate 1 refused (MUSIC)" in ln for ln in lines), lines)

    def test_a_tool_refusal_is_still_not_retried(self):
        cands = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
        self.inbox([row(1)])
        calls, _ = self.make(moments=(4000, cands), picks={1: (1, TOOL)}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1])
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.FAILED)


if __name__ == "__main__":
    unittest.main()
