import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"]="1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())
"""The clip-fixer task (prompts/fixer.md, 2026-10-22) may Try again a signed clip under its own name, and never sign,
pull or decline one: its name is accepted by retry alone and is never on the approvers list."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_posting_fixes import Base, DAY      # noqa: E402
from clipfactory import signoff as S          # noqa: E402


class FixerNameTest(Base):
    def test_1_the_fixer_may_try_again(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        lines = S.retry(self.cfg, self.acct, DAY, ["07:00"], S.FIXER)
        self.assertEqual(lines, ["  07:00: nothing gave up"])
        self.assertNotIn(S.FIXER, self.cfg.approvers)

    def test_2_the_fixer_never_signs_pulls_or_declines(self):
        self.ready("07:00")
        for act in (S.approve, S.pull):
            with self.assertRaises(SystemExit):
                act(self.cfg, self.acct, DAY, ["07:00"], S.FIXER)
        with self.assertRaises(SystemExit):
            S._who(self.cfg, S.FIXER, self.acct)


if __name__ == "__main__":
    unittest.main()
