"""The small statistics the learner needs, in plain stdlib Python (no numpy on the launchd Python 3.9).

Each answer is something a person can check by hand on a short list, which is what the tests do:

    median([3, 1, 2])                     2
    quantile([1, 2, 3, 4], 0.25)          1.75 (linear between the closest ranks, as a spreadsheet does)
    bootstrap_ci(a, b, n=2000, seed=7)    (lo, hi): a 90% range for median(a) - median(b), resampled with a fixed seed
    mann_whitney(a, b)                    (U of a, two-sided p): is a usually above or below b, by ranks (normal
                                          approximation with the tie and continuity corrections)
    bh([0.01, 0.04, 0.03])                Benjamini-Hochberg q values, in the order given
    spearman(x, y)                        the rank correlation (average ranks for ties), None when it cannot be said
    fisher_ci(rho, n)                     a 90% range for a correlation
    split_half(a, b)                      does a's lead over b (over all) point the same way in the older and the newer half

The learner reads a group's lift as exp(median(rel in the group) - median(rel in the rest)), so a range for the
difference of medians is a range for the lift once exponentiated.
"""
from __future__ import annotations

import math
import random
from typing import List, Optional, Sequence, Tuple

Z90 = 1.6448536269514722          # the normal quantile for a two-sided 90% range


def _clean(xs: Sequence) -> List[float]:
    out: List[float] = []
    for x in xs or []:
        if isinstance(x, bool) or x is None:
            continue
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def median(xs: Sequence) -> Optional[float]:
    """The middle value (the mean of the two middle ones for an even count); None for an empty list."""
    v = sorted(_clean(xs))
    n = len(v)
    if not n:
        return None
    mid = n // 2
    return v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2.0


def mean(xs: Sequence) -> Optional[float]:
    v = _clean(xs)
    return sum(v) / len(v) if v else None


def quantile(xs: Sequence, q: float) -> Optional[float]:
    """The q quantile (0 to 1), linear between the closest ranks (h = (n - 1) q)."""
    v = sorted(_clean(xs))
    if not v:
        return None
    q = min(1.0, max(0.0, float(q)))
    h = (len(v) - 1) * q
    lo = int(math.floor(h))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (h - lo) * (v[hi] - v[lo])


def _median_sorted(v: List[float]) -> float:
    n = len(v)
    mid = n // 2
    return v[mid] if n % 2 else (v[mid - 1] + v[mid]) / 2.0


def bootstrap_ci(a: Sequence, b: Sequence, n: int = 2000, seed: int = 0,
                 level: float = 0.90) -> Optional[Tuple[float, float]]:
    """A `level` range for median(a) - median(b): both lists resampled with replacement `n` times from one seeded
    generator (the same seed gives the same range), the middle `level` of the differences. None when a list is
    empty."""
    a, b = _clean(a), _clean(b)
    if not a or not b:
        return None
    rng = random.Random(seed)
    na, nb = len(a), len(b)
    diffs: List[float] = []
    for _ in range(max(1, int(n))):
        ra = sorted(rng.choices(a, k=na))
        rb = sorted(rng.choices(b, k=nb))
        diffs.append(_median_sorted(ra) - _median_sorted(rb))
    tail = (1.0 - float(level)) / 2.0
    return (quantile(diffs, tail), quantile(diffs, 1.0 - tail))     # type: ignore[return-value]


def ranks(xs: Sequence[float]) -> List[float]:
    """1-based ranks, ties given the average of the ranks they span."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def mann_whitney(a: Sequence, b: Sequence) -> Tuple[float, float]:
    """(U of a, two-sided p). U counts the pairs where a is above b (a tie counts a half). p from the normal
    approximation with the tie correction and a continuity correction of 0.5; 1.0 when it cannot be told (an
    empty side, or every value the same)."""
    a, b = _clean(a), _clean(b)
    na, nb = len(a), len(b)
    if not na or not nb:
        return (0.0, 1.0)
    allv = a + b
    r = ranks(allv)
    ra = sum(r[:na])
    u = ra - na * (na + 1) / 2.0
    n = na + nb
    ties: dict = {}
    for v in allv:
        ties[v] = ties.get(v, 0) + 1
    tie = sum(t ** 3 - t for t in ties.values())
    var = na * nb / 12.0 * ((n + 1) - tie / (n * (n - 1))) if n > 1 else 0.0
    if var <= 0:
        return (u, 1.0)
    mu = na * nb / 2.0
    d = u - mu
    d = max(0.0, abs(d) - 0.5)
    z = d / math.sqrt(var)
    p = 2.0 * (1.0 - norm_cdf(z))
    return (u, min(1.0, max(0.0, p)))


def bh(ps: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg q values (each at most 1, never below a smaller p's), in the order given."""
    m = len(ps)
    if not m:
        return []
    order = sorted(range(m), key=lambda i: ps[i])
    q = [1.0] * m
    best = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        best = min(best, float(ps[i]) * m / rank)
        q[i] = min(1.0, best)
    return q


def spearman(x: Sequence, y: Sequence) -> Optional[float]:
    """The rank correlation of paired lists (pairs with a missing value dropped); None under 3 pairs or when
    one side never changes."""
    pairs = []
    for a, b in zip(x or [], y or []):
        ca, cb = _clean([a]), _clean([b])
        if ca and cb:
            pairs.append((ca[0], cb[0]))
    if len(pairs) < 3:
        return None
    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((u - mx) * (v - my) for u, v in zip(rx, ry))
    sxx = sum((u - mx) ** 2 for u in rx)
    syy = sum((v - my) ** 2 for v in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def fisher_ci(rho: Optional[float], n: int, level: float = 0.90) -> Optional[Tuple[float, float]]:
    """A range for a correlation (Fisher's z, standard error 1/sqrt(n - 3)); None under 4 pairs."""
    if rho is None or n < 4:
        return None
    r = max(-0.999999, min(0.999999, float(rho)))
    z = math.atanh(r)
    zq = Z90 if abs(level - 0.90) < 1e-9 else _z_of(level)
    se = 1.0 / math.sqrt(n - 3)
    return (math.tanh(z - zq * se), math.tanh(z + zq * se))


def _z_of(level: float) -> float:
    """The two-sided normal quantile for a level (bisection on norm_cdf)."""
    target = 1.0 - (1.0 - level) / 2.0
    lo, hi = 0.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if norm_cdf(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def split_half(a: Sequence[Tuple[float, float]], b: Sequence[Tuple[float, float]],
               min_each: int = 2) -> Optional[bool]:
    """a and b are (time, value) pairs: the group and the rest. Every pair is split at the middle time into an
    older and a newer half; True when median(a) - median(b) has the same non-zero sign in both halves AS IT HAS over
    all the pairs (the finding being checked), False when a half disagrees with that or shows no difference, None
    when a half has under `min_each` on a side. Both halves pointing the other way to the whole is False: that is an
    era effect (the group sat mostly in a better or worse stretch), not a finding that holds over time."""
    pts = [(float(t), float(v), 0) for t, v in a] + [(float(t), float(v), 1) for t, v in b]
    if len(pts) < 2 * 2 * min_each:
        return None

    def _sign(xs: List[float], ys: List[float]) -> int:
        d = (median(xs) or 0.0) - (median(ys) or 0.0)
        return 0 if abs(d) < 1e-12 else (1 if d > 0 else -1)

    pts.sort(key=lambda p: p[0])
    half = len(pts) // 2
    signs = []
    for part in (pts[:half], pts[half:]):
        ga = [v for _, v, w in part if w == 0]
        gb = [v for _, v, w in part if w == 1]
        if len(ga) < min_each or len(gb) < min_each:
            return None
        signs.append(_sign(ga, gb))
    whole = _sign([v for _, v, w in pts if w == 0], [v for _, v, w in pts if w == 1])
    return whole != 0 and signs[0] == whole and signs[1] == whole
