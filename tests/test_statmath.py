import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The learner's statistics (clipfactory/statmath.py, 2026-10-20) against answers worked out by hand on short lists,
# so a person can check each one with a pencil. The bootstrap is held to its fixed seed and to the cases where the
# answer is certain.

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import statmath as SM     # noqa: E402


class MiddleTest(unittest.TestCase):
    def test_median_and_quantile(self):
        self.assertEqual(SM.median([3, 1, 2]), 2)
        self.assertEqual(SM.median([4, 1, 3, 2]), 2.5)
        self.assertIsNone(SM.median([]))
        self.assertEqual(SM.median([None, "x", 5, float("nan")]), 5)
        self.assertAlmostEqual(SM.quantile([1, 2, 3, 4], 0.25), 1.75)
        self.assertAlmostEqual(SM.quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertEqual(SM.quantile([1, 2, 3, 4], 1), 4)
        self.assertEqual(SM.quantile([7], 0.9), 7)
        self.assertAlmostEqual(SM.mean([1, 2, 6]), 3)

    def test_ranks_share_ties(self):
        self.assertEqual(SM.ranks([10, 20, 20, 30]), [1, 2.5, 2.5, 4])


class TestsTest(unittest.TestCase):
    def test_mann_whitney_without_ties(self):
        # a = 1 2 3, b = 4 5 6: rank sum of a 6, U = 6 - 3*4/2 = 0; var = 9/12 * 7 = 5.25; mu 4.5;
        # z = (4.5 - 0.5) / sqrt(5.25) = 1.7457; p = 2 (1 - Phi(1.7457)) = 0.0809
        u, p = SM.mann_whitney([1, 2, 3], [4, 5, 6])
        self.assertEqual(u, 0)
        self.assertAlmostEqual(p, 0.0809, places=3)
        u2, p2 = SM.mann_whitney([4, 5, 6], [1, 2, 3])
        self.assertEqual(u2, 9)
        self.assertAlmostEqual(p2, p)

    def test_mann_whitney_with_ties(self):
        # all = 1 2 2 2 3 4: ranks 1, 3 3 3, 5, 6; a = 1 2 2 has rank sum 7, U = 1; ties 3^3-3 = 24;
        # var = 9/12 * (7 - 24/30) = 4.65; z = (4.5 - 1 - 0.5) / sqrt(4.65) = 1.3912; p = 0.1642
        u, p = SM.mann_whitney([1, 2, 2], [2, 3, 4])
        self.assertEqual(u, 1)
        self.assertAlmostEqual(p, 0.1642, places=3)
        self.assertEqual(SM.mann_whitney([], [1]), (0.0, 1.0))
        self.assertEqual(SM.mann_whitney([2, 2], [2, 2])[1], 1.0)

    def test_bh(self):
        # sorted 0.01 0.03 0.04 (m = 3): 0.04*3/3 = 0.04; 0.03*3/2 = 0.045 -> 0.04; 0.01*3/1 = 0.03
        self.assertEqual([round(q, 6) for q in SM.bh([0.01, 0.04, 0.03])], [0.03, 0.04, 0.04])
        self.assertEqual(SM.bh([]), [])
        self.assertEqual(SM.bh([0.9, 0.8]), [0.9, 0.9])


class CorrelationTest(unittest.TestCase):
    def test_spearman(self):
        self.assertAlmostEqual(SM.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(SM.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        # d = -1 1 -1 1 0, sum d^2 = 4: rho = 1 - 6*4 / (5*24) = 0.8
        self.assertAlmostEqual(SM.spearman([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]), 0.8)
        self.assertIsNone(SM.spearman([1, 2], [1, 2]))
        self.assertIsNone(SM.spearman([1, 1, 1], [1, 2, 3]))

    def test_fisher_range(self):
        # z = atanh(0.5) = 0.549306, se = 1/sqrt(25) = 0.2, +-1.644854*0.2 = 0.328971
        lo, hi = SM.fisher_ci(0.5, 28)
        self.assertAlmostEqual(lo, math.tanh(0.549306 - 0.328971), places=5)
        self.assertAlmostEqual(hi, math.tanh(0.549306 + 0.328971), places=5)
        self.assertAlmostEqual(lo, 0.2168, places=3)
        self.assertAlmostEqual(hi, 0.7059, places=3)
        self.assertIsNone(SM.fisher_ci(0.5, 3))


class BootstrapTest(unittest.TestCase):
    def test_a_certain_answer(self):
        self.assertEqual(SM.bootstrap_ci([2, 2, 2], [1, 1], n=300, seed=1), (1.0, 1.0))
        self.assertIsNone(SM.bootstrap_ci([], [1]))

    def test_the_seed_fixes_the_range(self):
        a = [1.0, 1.4, 0.9, 1.6, 1.2, 1.1, 1.3, 0.8, 1.5, 1.0]
        b = [0.2, 0.0, 0.3, -0.1, 0.4, 0.1, 0.2, 0.0, 0.3, 0.1]
        one = SM.bootstrap_ci(a, b, n=2000, seed=7)
        self.assertEqual(one, SM.bootstrap_ci(a, b, n=2000, seed=7))
        lo, hi = one
        true = SM.median(a) - SM.median(b)          # 1.15 - 0.15 = 1.0
        self.assertAlmostEqual(true, 1.0)
        self.assertLessEqual(lo, true)
        self.assertGreaterEqual(hi, true)
        self.assertGreater(lo, 0.5)                 # the groups never overlap: the range stays well above 0
        self.assertLess(hi, 1.5)


class SplitHalfTest(unittest.TestCase):
    def test_same_way_in_both_halves(self):
        a = [(1, 5), (2, 5), (5, 6), (6, 6)]
        b = [(3, 1), (4, 1), (7, 2), (8, 2)]
        self.assertTrue(SM.split_half(a, b))

    def test_turns_round(self):
        a = [(1, 5), (2, 5), (5, 0), (6, 0)]
        b = [(3, 1), (4, 1), (7, 2), (8, 2)]
        self.assertFalse(SM.split_half(a, b))

    def test_both_halves_against_the_whole_is_not_holding(self):
        # Review fix 2026-10-20: the halves agreed with each other but not with the finding. Over all the pairs a's
        # median is 10 and b's is 1 (a leads by 9), yet a sits BELOW b in the older half (10 vs 11) and in the newer
        # half (0 vs 1); a just sat mostly in the better stretch. That is an era effect, so it must not read as holding.
        a = [(1, 10), (2, 10), (3, 10), (4, 10), (5, 10), (11, 0), (12, 0)]
        b = [(6, 11), (7, 11), (13, 1), (14, 1), (15, 1), (16, 1), (17, 1)]
        self.assertFalse(SM.split_half(a, b))
        # The same lists with the lead the other way in each half (a above b in both) do hold.
        a2 = [(1, 12), (2, 12), (3, 12), (4, 12), (5, 12), (11, 2), (12, 2)]
        self.assertTrue(SM.split_half(a2, b))

    def test_too_few(self):
        self.assertIsNone(SM.split_half([(1, 1)], [(2, 2)]))
        self.assertIsNone(SM.split_half([(1, 1), (2, 1), (3, 1), (4, 1)], [(5, 0), (6, 0), (7, 0), (8, 0)]))


if __name__ == "__main__":
    unittest.main()
