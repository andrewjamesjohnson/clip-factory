import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The learner (clipfactory/learn.py, 2026-10-20): the 24-hour number from checkpoints or snapshots, the baseline
# (the usual: five earlier posts on the same platform, none until then) and rel_24h, how sure a finding is (and when
# nothing may be claimed), views lost ranked on a 60-post fixture, a sudden drop and a platform gap as problems,
# "Which" lists that end "and N more", the whitelist, clamps and expiry, "did it work" and its revert, experiments
# from assignment to conclusion, verdict counts, the daily job's switch, and the report's words. Throwaway folders;
# nothing reaches a platform.

import contextlib
import io
import json
import math
import random
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from social_fakes import cfg_in_tmp          # noqa: E402

from clipfactory import learn as L           # noqa: E402
from clipfactory import playbook as PB       # noqa: E402
from clipfactory import social as SOC        # noqa: E402
from clipfactory import stats as ST          # noqa: E402
from clipfactory import store                # noqa: E402
from clipfactory import verdicts as VD       # noqa: E402

D = date(2031, 7, 14)
BASE = {"youtube_shorts": 100, "facebook_reels": 200, "instagram_reels": 150}
SLOTS = ("07:00", "10:30", "14:00", "19:15")
MULT = (0.8, 0.9, 1.1, 1.2)
L.BOOT_N = 300                               # the bootstrap's resamples: fewer in tests (the seed still fixes them)


def utc(t: datetime) -> str:
    return t.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.now = datetime(2031, 7, 15, 5, 0, tzinfo=self.cfg.tz())

    # ------------------------------------------------------------------------------------------- fixtures
    def switch(self, acct, key, value):
        with store.edit(store.path(acct.slug, "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})[key] = value

    def board(self, day: date, slots: dict) -> None:
        store.write(store.path(self.gta.slug, f"{day}.json"),
                    {"account": self.gta.slug, "name": self.gta.name, "date": day.isoformat(), "slots": slots})

    def slot(self, day: date, k: str, status="scheduled", fmt="underlay", host="MrBossFTW", **kw) -> dict:
        s = {"slot": k, "publish_at": self.cfg.slot_time(day, k).isoformat(), "format": fmt, "status": status,
             "notes": [f"2031-01-01T00:00:00+00:00 {status} here"], "host": host, "slug": f"clip-{day:%m%d}-{k[:2]}",
             "source_title": f"Clip {day} {k}", "clip": {"sha256": f"sha{day:%m%d}{k[:2]}", "seconds": 40.0},
             "outlets": {}}
        s.update(kw)
        return s

    def post(self, s: dict, outlet: str, v24, late_min: float = 0.0) -> None:
        went = datetime.fromisoformat(s["publish_at"]) + timedelta(minutes=late_min)
        pid = f"{outlet[:2]}{s['slug']}"
        rec = {"post_id": pid, "platform_id": pid, "url": f"https://example.com/{pid}", "published_at": utc(went),
               "provider": "direct", "metrics": {"views": int((v24 or 0) * 1.2), "likes": 0, "comments": 0,
                                                 "shares": 0, "at": utc(went + timedelta(hours=30))}}
        if outlet == "youtube_shorts":
            rec = dict(rec, video_id=pid, publish_at=s["publish_at"])
        s["outlets"][outlet] = rec
        if v24 is not None:
            p = store.path("stats", "series", f"{self.gta.slug}__{outlet}__{went:%Y-%m}.json")
            with store.edit(p, dict) as ser:
                ser[pid] = {"went": utc(went), "points": [{"label": "24h", "views": v24, "age_min": 1440}]}

    def sixty(self) -> None:
        """5 days x 4 slots x 3 platforms of posts before D (room clips at 1.5x), then D: two slots failed to make,
        one empty, one posted with Instagram given up (no cut) and Facebook 3 hours late."""
        for i in range(5, 0, -1):
            day = D - timedelta(days=i)
            slots = {}
            for j, k in enumerate(SLOTS):
                fmt = "room" if j % 2 == 0 else "gaggle"
                s = self.slot(day, k, fmt=fmt, host="C-SPAN" if j < 2 else "MrBossFTW")
                for o, b in BASE.items():
                    self.post(s, o, round(b * MULT[j] * (1.5 if fmt == "room" else 1.0)))
                slots[k] = s
            self.board(day, slots)
        s4 = self.slot(D, "12:15")
        self.post(s4, "youtube_shorts", None)
        self.post(s4, "facebook_reels", None, late_min=180)
        s4["outlets"]["instagram_reels"] = {"gave_up": True, "why": "no Instagram cut: the clip is 77s and Instagram "
                                                                    "takes 30-50s for this account"}
        self.board(D, {"07:00": self.slot(D, "07:00", status="failed"), "08:45": self.slot(D, "08:45", status="failed"),
                       "10:30": self.slot(D, "10:30", status="open"), "12:15": s4})


def rows_of(rels, outlet="youtube_shorts", start=None, **extra):
    """Rows with these rel_24h values, one a day."""
    start = start or datetime(2031, 6, 1, 12, tzinfo=timezone.utc)
    out = []
    for i, r in enumerate(rels):
        row = {"pid": f"p{len(rels)}-{i}-{outlet}", "outlet": outlet, "board": (start + timedelta(days=i)).date()
               .isoformat(), "slot": "07:00", "went": utc(start + timedelta(days=i)), "rel_24h": r,
               "views": {"24h": round(100 * math.exp(r))}, "baseline_24h": 100.0, "hour": 7, "dow": i % 7}
        row.update(extra)
        out.append(row)
    return out


# ================================================================================================= numbers
class TwentyFourHoursTest(unittest.TestCase):
    def test_checkpoint_wins(self):
        v, src = L.views_at([(1000, 50), (2000, 90)], {"24h": 77}, 3000)
        self.assertEqual((v["24h"], src), (77, "series"))

    def test_between_two_snapshots(self):
        v, src = L.views_at([(1200, 100), (1680, 140)], {}, 3000)          # 20 h and 28 h: half way = 120
        self.assertEqual((v["24h"], src), (120, "snapshots"))
        self.assertNotIn("72h", v)                                         # nothing either side of 72 h

    def test_stretched_and_capped(self):
        v, src = L.views_at([(2880, 1000)], {}, 3000)                      # only at 48 h: log curve, 24 h below it
        want = 1000 * math.log1p(12) / math.log1p(24)
        self.assertEqual((v["24h"], src), (round(want), "estimate"))
        v2, _ = L.views_at([(600, 900), (2880, 1000)], {}, 3000)           # never below a reading before 24 h
        self.assertEqual(v2["24h"], round(900 + 100 * (1440 - 600) / (2880 - 600)))
        v3, _ = L.views_at([(600, 900)], {}, 3000)                         # never under 900, the one reading
        self.assertGreaterEqual(v3["24h"], 900)
        self.assertEqual(L.views_at([(600, 900)], {}, 1000), ({}, ""))     # under 24 h old: no 24-hour number



class HistoryTest(Base):
    def test_from_history_snapshots(self):
        t = self
        if True:
            day = D - timedelta(days=2)
            s = t.slot(day, "07:00")
            t.post(s, "facebook_reels", None)
            t.board(day, {"07:00": s})
            went = datetime.fromisoformat(s["publish_at"])
            pid = s["outlets"]["facebook_reels"]["platform_id"]
            sid = ST.post_id({"account": "gaming", "platform": "facebook", "pid": pid})
            reads = [{"at": utc(went + timedelta(hours=h)), "posts": {sid: [v, 0, 0, 0, int(went.timestamp())]}}
                     for h, v in ((20, 400), (28, 480))]
            store.write(store.path("stats", "history", f"{day}.json"), {"day": str(day), "reads": reads})
            rows = L.rows_for(t.cfg, t.gta, t.now)
            self.assertEqual(rows[0]["views"]["24h"], 440)
            self.assertEqual(rows[0]["v24_src"], "snapshots")


class BaselineTest(unittest.TestCase):
    """A post's usual: the median of the same account and platform's posts that went up BEFORE it, 14 days back (30
    when fewer than 5), at least 5 of them; never the post itself, never a later post, never another platform."""
    def yt(self, vals, start=datetime(2031, 6, 1, tzinfo=timezone.utc), outlet="youtube_shorts", step=1):
        return [{"outlet": outlet, "went": utc(start + timedelta(days=i * step)), "views": {"24h": v}}
                for i, v in enumerate(vals)]

    def test_only_five_earlier_posts_make_a_usual(self):
        rows = self.yt([10, 20, 30, 40, 50, 60, 4])
        L.add_baselines(rows)
        self.assertEqual([r["baseline_24h"] for r in rows[:5]], [None] * 5)   # the first five have no usual
        self.assertEqual([r["rel_24h"] for r in rows[:5]], [None] * 5)
        self.assertEqual(rows[5]["baseline_24h"], 30)                     # 10 20 30 40 50: not itself (60)
        self.assertEqual(rows[5]["baseline_n"], 5)
        self.assertAlmostEqual(rows[5]["rel_24h"], round(math.log(61 / 31), 4))
        self.assertEqual(rows[6]["baseline_24h"], 35)                     # 10 to 60: never a later post

    def test_a_later_post_never_counts(self):
        early = self.yt([8, 9, 7, 10, 6])
        late = self.yt([5000] * 6, start=datetime(2031, 6, 10, tzinfo=timezone.utc))
        rows = early + late
        L.add_baselines(rows)
        self.assertIsNone(rows[3]["baseline_24h"])                        # a 10-view post is never "under 0.1x"
        self.assertEqual(rows[5]["baseline_24h"], 8)                      # the first big one: against 8 9 7 10 6

    def test_30_days_then_none(self):
        start = datetime(2031, 6, 1, tzinfo=timezone.utc)
        rows = self.yt([100, 100, 100, 100, 100], start) + self.yt([400], start + timedelta(days=20)) \
            + self.yt([50], start + timedelta(days=40))
        L.add_baselines(rows)
        self.assertEqual((rows[5]["baseline_24h"], rows[5]["baseline_n"]), (100, 5))   # 14 days: none; 30: five
        self.assertIsNone(rows[6]["baseline_24h"])                        # 30 days back: only the day-20 post
        self.assertEqual(L.baseline_for(rows, "youtube_shorts", start + timedelta(days=21)), (100, 6))
        self.assertEqual(L.baseline_for(rows, "youtube_shorts", start + timedelta(days=41)), (None, 2))

    def test_platforms_are_never_pooled(self):
        rows = self.yt([10, 20, 30, 40, 50]) + self.yt([70], start=datetime(2031, 6, 9, tzinfo=timezone.utc),
                                                        outlet="facebook_reels")
        L.add_baselines(rows)
        self.assertIsNone(rows[5]["baseline_24h"])                        # five YouTube posts are no Facebook usual
        same = self.yt([10, 10, 10, 10, 10, 10], step=0)                  # all at one moment: none is before another
        L.add_baselines(same)
        self.assertEqual({r["baseline_24h"] for r in same}, {None})

    def test_a_post_without_its_own_number_still_has_a_usual(self):
        rows = self.yt([10, 20, 30, 40, 50]) + [{"outlet": "youtube_shorts", "views": {},
                                                 "went": utc(datetime(2031, 6, 7, tzinfo=timezone.utc))}]
        L.add_baselines(rows)
        self.assertEqual((rows[5]["baseline_24h"], rows[5]["rel_24h"]), (30, None))   # late posts are costed by it


# ================================================================================================= how sure
class HowSureTest(unittest.TestCase):
    def test_gates(self):
        self.assertEqual(L.confidence(25, 25, [1.3, 2.0], 0.05, True, 50), "strong")
        self.assertEqual(L.confidence(25, 25, [1.3, 2.0], 0.15, True, 50), "likely")
        self.assertEqual(L.confidence(14, 16, [1.3, 2.0], 0.05, True, 30), "likely")
        self.assertEqual(L.confidence(14, 16, [1.3, 2.0], 0.05, False, 30), "early sign")   # halves disagree
        self.assertEqual(L.confidence(25, 25, [0.9, 2.0], 0.05, True, 50), "early sign")    # range takes in 1x
        self.assertEqual(L.confidence(25, 25, [1.3, 2.0], 0.05, True, 29), "early sign")    # under 30 posts
        self.assertEqual(L.confidence(5, 25, [1.3, 2.0], 0.01, True, 50), "")               # under 6 a side
        self.assertEqual(L.confidence(14, 16, [1.3, 2.0], 0.05, True, 30, min_n=15), "early sign")

    def noisy(self, n_a, n_b, lift_a, seed=3):
        rng = random.Random(seed)
        a = rows_of([lift_a + rng.uniform(-0.2, 0.2) for _ in range(n_a)], format="room")
        b = rows_of([rng.uniform(-0.2, 0.2) for _ in range(n_b)], start=datetime(2031, 6, 1, 18, tzinfo=timezone.utc),
                    format="gaggle")
        for r in b:
            r["pid"] += "b"
        return a + b

    def test_a_clear_format_is_strong(self):
        works = L.analyse(self.noisy(30, 30, 1.0), day="2031-07-14")
        room = [w for w in works if w["factor"] == "format" and w["level"] == "room"][0]
        self.assertEqual(room["confidence"], "strong")
        self.assertGreater(room["lift"], 2.3)
        self.assertGreater(room["ci90"][0], 1.0)
        self.assertEqual(works, L.analyse(self.noisy(30, 30, 1.0), day="2031-07-14"))    # seeded: the same twice

    def test_no_claims_under_30_posts(self):
        works = L.analyse(self.noisy(10, 10, 1.0), day="d")
        self.assertTrue(works)
        self.assertEqual({w["confidence"] for w in works}, {"early sign"})

    def test_under_six_is_not_shown(self):
        self.assertEqual([w for w in L.analyse(self.noisy(5, 40, 1.0), day="d") if w["factor"] == "format"], [])

    def test_platforms_never_pooled(self):
        rows = rows_of([1.0] * 8, "youtube_shorts", format="room") + rows_of([0.0] * 8, "facebook_reels",
                                                                             format="gaggle")
        self.assertEqual([w for w in L.analyse(rows, day="d") if w["factor"] == "format"], [])


# ================================================================================================= views lost
class ViewsLostTest(Base):
    def test_the_sixty_post_ranking(self):
        self.sixty()
        rows = L.rows_for(self.cfg, self.gta, self.now)
        posted = [r for r in rows if r["board"] < D.isoformat()]
        self.assertEqual(len(posted), 60)
        self.assertTrue(all(r["v24_src"] == "series" for r in posted))
        rep = L.report(self.cfg, self.gta, day=D, now=self.now)
        inc = [p for p in rep["problems"] if not p["overlaps"]]
        self.assertEqual([p["key"] for p in inc], ["failed", "empty", "no_cut:instagram_reels", "late"])
        L.add_baselines(rows)
        noon = datetime(D.year, D.month, D.day, 12, tzinfo=self.cfg.tz())
        base = {o: L.baseline_for(rows, o, noon)[0] for o in BASE}
        total = sum(base.values())
        got = {p["key"]: p for p in inc}
        self.assertEqual(got["failed"]["n"], 2)
        self.assertEqual(got["failed"]["views_lost"], round(2 * total))
        self.assertEqual(got["empty"]["n"], 1)
        self.assertEqual(got["empty"]["views_lost"], round(total))
        self.assertEqual([p for p in rep["problems"] if p["overlaps"]], [])  # early signs are not problems
        self.assertEqual(got["no_cut:instagram_reels"]["views_lost"], round(base["instagram_reels"]))
        self.assertEqual(got["late"]["views_lost"], round(base["facebook_reels"] * 3 / 24))
        self.assertEqual(rep["kpis"]["views_lost"], sum(p["views_lost"] for p in inc))
        self.assertIn("night-retry gaming", got["failed"]["fix"])
        self.assertEqual(got["failed"]["href"], f"/?date={D}&acct=gaming")
        self.assertEqual([e["slot"] for e in got["failed"]["evidence"]], ["07:00", "08:45"])

    def test_the_report_files_and_words(self):
        self.sixty()
        rep = L.report(self.cfg, self.gta, day=D, now=self.now)
        md = L.report_path(self.gta, D, "md").read_text()
        self.assertEqual(json.loads(L.report_path(self.gta, D).read_text())["day"], D.isoformat())
        order = [md.index(h) for h in ("## Biggest problems", "## Best posts", "## Worst posts", "## What works",
                                       "## Coach", "## Publishing", "## Proposals")]
        self.assertEqual(order, sorted(order))
        self.assertIn("1. 2 slots failed to make: about", md)
        for bad in ("\u2014", "\u2013", "!", "p-value", " p "):
            self.assertNotIn(bad, md)
        self.assertTrue(rep["best"])
        self.assertTrue(all(b["rel"] > 0 for b in rep["best"]))
        self.assertTrue(all(w["rel"] < 0 for w in rep["worst"]))
        self.assertTrue(rep["best"][0]["why"][0].endswith("the usual"))
        feats = json.loads(L.features_path(self.gta).read_text())["rows"]
        need = {"pid", "outlet", "board", "slot", "hour", "dow", "slug", "format", "host", "creator_key", "rights",
                "source", "bench", "sent_s", "cut", "title_feat", "hook_feat", "coach", "extras", "experiment",
                "playbook_v", "went", "views", "views_now", "extra", "baseline_24h", "rel_24h"}
        self.assertLessEqual(need, set(feats[0]))
        self.assertEqual(rep["health"]["by_outlet"]["instagram_reels"]["gave_up"], 1)
        self.assertEqual(rep["health"]["by_outlet"]["facebook_reels"]["late"], 1)

    def test_a_declined_slot_says_why(self):
        self.sixty()
        bd = json.loads(store.path("gaming", f"{D}.json").read_text())
        bd["slots"]["10:30"]["status"] = "declined"
        store.write(store.path("gaming", f"{D}.json"), bd)
        VD.record(self.cfg, self.gta, D, bd["slots"]["10:30"], "decline", "Andrew", ["weak_hook", "framing"])
        rep = L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        dec = [p for p in rep["problems"] if p["key"] == "declined"][0]
        self.assertEqual(dec["words"], "1 declined clip not replaced (hook, framing)")   # old code read as hook


class VerdictCountsTest(Base):
    def test_counts(self):
        s = self.slot(D, "07:00")
        VD.record(self.cfg, self.gta, D, s, "approve", "Andrew")
        VD.record(self.cfg, self.gta, D, s, "decline", "Andrew", ["weak_hook", "framing"])
        VD.record(self.cfg, self.gta, D - timedelta(days=1), s, "remake", "Andrew", ["weak_hook"])
        VD.record(self.cfg, self.gta, D - timedelta(days=9), s, "decline", "Andrew", ["audio"])
        got = L.verdict_counts(self.gta, D - timedelta(days=6), D)
        self.assertEqual((got["approve"], got["decline"], got["remake"]), (1, 1, 1))
        self.assertEqual(got["reasons"], {"hook": 2, "framing": 1})        # the first list's codes count as today's


# ================================================================================================= proposals
def work(factor="creator", level="mrbossftw", lift=1.69, outlet="youtube_shorts", conf="strong", n=24):
    return {"factor": factor, "level": level, "outlet": outlet, "words": L.factor_words(factor, level), "n": n,
            "n_rest": 30, "lift": lift, "ci90": [1.2, 2.2], "p": 0.01, "q": 0.04, "split_half": True,
            "confidence": conf, "outlet_n": 54}


class WhitelistTest(Base):
    def test_only_the_whitelist_goes_in(self):
        for key in ("scout.half_life_h", "scout.rank.caps_heat", "makers.music_cap", "config.slots", "coach.secret"):
            with self.assertRaises(SystemExit):
                PB.check(key, 1.2)
        self.assertEqual(PB.check("scout.channels.MrBossFTW", 1.25), ("scout.channels.mrbossftw", 1.25))
        with self.assertRaises(SystemExit):
            PB.check("runbook.x", "Lead with the answer \u2014 always")
        with self.assertRaises(SystemExit):
            PB.check("coach.length_best.youtube_shorts", [60, 30])

    def test_advice_changes_nothing(self):
        with store.edit(L.proposals_path(self.gta), dict) as props:
            pid = L._put(self.gta, L._from_work(self.gta, work("hour", "06:00 to 09:00"), []), props,
                         datetime(2031, 7, 15, tzinfo=timezone.utc))
        self.assertEqual(L.proposals(self.gta)[pid]["kind"], "advice")
        self.assertIn("noted", L.approve(self.cfg, self.gta, pid, "Andrew"))
        self.assertEqual(PB.read(self.gta)["version"], 0)

    def test_the_weekly_file_may_add_four_kinds_only(self):
        f = Path(self.tmp) / "props.json"
        f.write_text(json.dumps([{"kind": "factor", "words": "rank him up", "to": 1.5}]))
        with self.assertRaises(SystemExit):
            L.propose_file(self.cfg, self.gta, f, "clip-weekly-report")
        f.write_text(json.dumps([{"kind": "experiment", "words": "Numbers", "to": {"question": "A number?",
                                                                                 "kind": "tag"}}]))
        with self.assertRaises(SystemExit):                  # a label-only test changes nothing: not an experiment
            L.propose_file(self.cfg, self.gta, f, "clip-weekly-report")
        f.write_text(json.dumps([
            {"kind": "runbook_note", "words": "Cut to the answer", "to": "Lead with the answer, not the question."},
            {"kind": "advice", "words": "Post Politics gaggles before noon"},
            {"kind": "experiment", "words": "First comments", "to": {"question": "Does a first comment help?",
                                                                     "kind": "first_comment", "assign": "alternate"}},
            {"kind": "draft_rule", "words": "Answer where is this from", "to": {"intent": "asks_source", "n": 9}}]))
        made = L.propose_file(self.cfg, self.gta, f, "clip-weekly-report")
        self.assertEqual(len(made), 4)
        props = L.proposals(self.gta)
        self.assertEqual(sorted(props[m]["kind"] for m in made), ["advice", "draft_rule", "experiment",
                                                                   "runbook_note"])
        rb = [m for m in made if props[m]["kind"] == "runbook_note"][0]
        L.approve(self.cfg, self.gta, rb, "Andrew")
        self.assertEqual(PB.read(self.gta)["runbook"], ["Lead with the answer, not the question."])
        ex = [m for m in made if props[m]["kind"] == "experiment"][0]
        self.assertIn("started", L.approve(self.cfg, self.gta, ex, "Andrew"))
        self.assertEqual([e["state"] for e in L.experiments(self.gta).values()], ["running"])
        self.assertEqual(L.propose_file(self.cfg, self.gta, f, "clip-weekly-report"), [])   # already decided or open


class ClampsAndExpiryTest(Base):
    def test_factor_clamps(self):
        t0 = datetime(2031, 7, 1, tzinfo=timezone.utc)
        PB.apply(self.cfg, self.gta, {"key": "scout.channels.a", "to": 3.0}, "Andrew", t0)
        PB.apply(self.cfg, self.gta, {"key": "scout.people.b", "to": 0.1}, "Andrew", t0)
        sc = PB.read(self.gta, t0)["scout"]
        self.assertEqual((sc["channels"]["a"], sc["people"]["b"]), (1.6, 0.5))
        prop = L._from_work(self.gta, work(lift=4.0), [])
        self.assertEqual(prop["to"], 1.6)

    def test_rules_end_after_60_days(self):
        t0 = datetime(2031, 7, 1, tzinfo=timezone.utc)
        said = PB.apply(self.cfg, self.gta, {"key": "scout.channels.a", "to": 1.3}, "Andrew", t0)
        self.assertIn("until 2031-08-30", said)
        self.assertEqual(len(PB.read(self.gta, t0 + timedelta(days=59))["rules"]), 1)
        self.assertEqual(PB.read(self.gta, t0 + timedelta(days=61))["rules"], [])
        self.assertEqual(PB.read(self.gta, t0 + timedelta(days=61))["scout"]["channels"], {})

    def test_proposals_end_after_14_days_and_decline_holds_until_n_doubles(self):
        t0 = datetime(2031, 7, 1, tzinfo=timezone.utc)
        with store.edit(L.proposals_path(self.gta), dict) as props:
            pid = L._put(self.gta, dict(L._from_work(self.gta, work(), []), evidence={"n": 24}), props, t0)
            L._expire(props, t0 + timedelta(days=13))
            self.assertEqual(props[pid]["state"], "open")
            L._expire(props, t0 + timedelta(days=15))
            self.assertEqual(props[pid]["state"], "expired")
        with store.edit(L.proposals_path(self.gta), dict) as props:
            p2 = L._put(self.gta, dict(L._from_work(self.gta, work(), []), evidence={"n": 24}), props, t0)
        self.assertTrue(p2)
        L.decline(self.cfg, self.gta, p2, "Andrew", "not_enough", "wait", now=t0)
        with store.edit(L.proposals_path(self.gta), dict) as props:
            self.assertIsNone(L._put(self.gta, dict(L._from_work(self.gta, work(), []), evidence={"n": 40}), props, t0))
            self.assertTrue(L._put(self.gta, dict(L._from_work(self.gta, work(), []), evidence={"n": 48}), props, t0))

    def test_test_it_first_makes_an_experiment(self):
        fc = work("first_comment", True, lift=1.3, outlet="facebook_reels")
        with store.edit(L.proposals_path(self.gta), dict) as props:
            pid = L._put(self.gta, dict(L._from_work(self.gta, fc, []), evidence=L._evidence(fc, "w")), props,
                         datetime(2031, 7, 1, tzinfo=timezone.utc))
        said = L.decline(self.cfg, self.gta, pid, "Andrew", "test_first")
        self.assertIn("experiment e-0001", said)
        e = L.experiments(self.gta)["e-0001"]
        self.assertEqual((e["state"], e["proposal"], e["signed"]), ("draft", pid, None))
        self.assertEqual((e["kind"], e["outlets"]), ("first_comment", ["facebook_reels"]))
        with self.assertRaises(SystemExit):
            L.decline(self.cfg, self.gta, pid, "Andrew", "other")                    # already decided

    def test_approve_a_factor(self):
        with store.edit(L.proposals_path(self.gta), dict) as props:
            pid = L._put(self.gta, dict(L._from_work(self.gta, work(lift=1.69), []), evidence={"n": 24}), props,
                         datetime(2031, 7, 1, tzinfo=timezone.utc))
        self.assertEqual(L.proposals(self.gta)[pid]["to"], 1.3)                      # sqrt(1.69): half the lift
        L.approve(self.cfg, self.gta, pid, "Andrew")
        pb = PB.read(self.gta)
        self.assertEqual((pb["version"], pb["scout"]["channels"]), (1, {"mrbossftw": 1.3}))
        self.assertEqual(L.proposals(self.gta)[pid]["state"], "approved")
        self.assertEqual(PB.history(self.gta)[0]["proposal"], pid)


class DidItWorkTest(Base):
    def setUp(self):
        super().setUp()
        self.switch(self.gta, "learn.playbook", "on")               # no switch history: on all along

    def test_worse_files_a_revert(self):
        t0 = datetime(2031, 6, 20, 12, tzinfo=timezone.utc)
        now = t0 + timedelta(days=20)
        PB.apply(self.cfg, self.gta, {"key": "scout.channels.mrbossftw", "to": 1.3, "id": "p-x"}, "Andrew", t0)
        rows = (rows_of([0.5] * 5, start=t0 - timedelta(days=10), creator_key="mrbossftw")
                + rows_of([-0.5] * 5, start=t0 + timedelta(days=1), creator_key="mrbossftw"))
        for i, r in enumerate(rows):
            r["pid"] = f"d{i}"
        did = L.did_it_work(self.cfg, self.gta, rows, now)
        self.assertEqual(len(did), 1)
        self.assertEqual((did[0]["verdict"], did[0]["evidence"], did[0]["version_before"]), ("worse", "weak", 0))
        self.assertAlmostEqual(did[0]["change"], math.exp(-1), places=3)
        made = L.make_proposals(self.cfg, self.gta, [], rows, now, "w", did)
        p = L.proposals(self.gta)[made[0]]
        self.assertEqual((p["kind"], p["to"]["rule"]), ("revert", did[0]["rule"]))
        L.approve(self.cfg, self.gta, made[0], "Andrew", now)
        pb = PB.read(self.gta, now)
        self.assertEqual((pb["version"], pb["rules"]), (2, []))
        self.assertEqual(L.did_it_work(self.cfg, self.gta, rows, now), [])          # the rule is gone

    def test_too_early_and_too_few(self):
        t0 = datetime(2031, 6, 20, 12, tzinfo=timezone.utc)
        PB.apply(self.cfg, self.gta, {"key": "scout.channels.mrbossftw", "to": 1.3}, "Andrew", t0)
        self.assertEqual(L.did_it_work(self.cfg, self.gta, [], t0 + timedelta(days=10)), [])
        got = L.did_it_work(self.cfg, self.gta, [], t0 + timedelta(days=15))
        self.assertEqual(got[0]["verdict"], "too few posts to tell")


# ================================================================================================= experiments
class ExperimentTest(Base):
    def ctx(self, k: str, day=D):
        slot = self.slot(day, k)
        return SOC.CollectCtx(cfg=self.cfg, acct=self.gta, day=day, slot=slot, extras={"first_comment": {
            "facebook_reels": {"text": "Full video", "from": "default"},
            "youtube_shorts": {"text": "Full video", "from": "default"}}})

    def test_assignment_on_practice_and_off(self):
        eid = L.new_experiment(self.cfg, self.gta, question="Does the first comment help?", kind="first_comment",
                               arms={"A": "with it", "B": "without it"}, outlets=["facebook_reels"], by="Andrew",
                               signed=True)
        self.switch(self.gta, "first_comment.facebook_reels", "on")           # a first comment that is posted
        c = self.ctx("07:00")
        L.on_collect(c)                                                       # off: nothing
        self.assertEqual(L.experiments(self.gta)[eid]["posts"], {"A": [], "B": []})
        self.switch(self.gta, "learn.experiments", "practice")
        L.on_collect(c)
        self.assertNotIn("experiment", c.extras)                              # practice: the clip is unchanged
        self.assertEqual(L.experiments(self.gta)[eid]["posts"]["A"][0]["practice"], True)
        self.switch(self.gta, "learn.experiments", "on")
        arms = []
        for k in ("08:45", "10:30", "12:15"):
            c = self.ctx(k)
            L.on_collect(c)
            arms.append(c.extras["experiment"][eid])
            if c.extras["experiment"][eid] == "B":
                self.assertNotIn("facebook_reels", c.extras["first_comment"])
                self.assertIn("youtube_shorts", c.extras["first_comment"])     # only the experiment's platform
            else:
                self.assertIn("facebook_reels", c.extras["first_comment"])
        self.assertEqual(arms, ["B", "A", "B"])                               # taking turns after the practice A
        again = self.ctx("12:15")
        L.on_collect(again)                                                   # the same slot again: the same arm,
        self.assertEqual(again.extras["experiment"], {eid: "B"})              # carried out on the new clip
        self.assertNotIn("facebook_reels", again.extras["first_comment"])
        self.assertEqual(sum(1 for a in "AB" for p in L.experiments(self.gta)[eid]["posts"][a]
                             if p["slot"] == "12:15"), 1)
        card = L.card_part(self.cfg, self.gta, D, {"slot": "10:30"}, self.now)
        self.assertIn(f"Experiment {eid}", card)
        self.assertIn("arm A", card)

    def test_trial_reel_and_draft(self):
        self.switch(self.gta, "learn.experiments", "on")
        e1 = L.new_experiment(self.cfg, self.gta, question="Trial reels first?", kind="trial_reel", by="Andrew",
                              signed=True)
        e2 = L.new_experiment(self.cfg, self.gta, question="Unsigned", kind="first_comment", by="learner",
                              signed=False)
        c0 = self.ctx("06:00")
        L.on_collect(c0)                                     # trial reels switch off: written down, nothing changes
        self.assertNotIn("trial", c0.extras)
        self.assertEqual(L.experiments(self.gta)[e1]["posts"]["A"][0]["why"], "it waits for the trial reels switch")
        self.switch(self.gta, "trial_reels", "on")
        cs = [self.ctx(k) for k in ("07:00", "08:45")]
        for c in cs:
            L.on_collect(c)
        self.assertEqual(cs[0].extras["trial"]["instagram_reels"],     # the written-down 06:00 took arm A
                         {"graduation_strategy": "SS_PERFORMANCE", "experiment": e1, "arm": "B"})
        self.assertNotIn("trial", cs[1].extras)
        self.assertNotIn(e2, cs[0].extras["experiment"])                      # a draft assigns nothing
        from clipfactory import extras as EX
        EX.check(cs[0].extras)                                                # what it adds is a valid extras file
        L.sign_experiment(self.cfg, self.gta, e2, "Andrew")
        L.stop_experiment(self.cfg, self.gta, e2, "Andrew")
        self.assertEqual(L.experiments(self.gta)[e2]["state"], "stopped")
        with self.assertRaises(SystemExit):
            L.sign_experiment(self.cfg, self.gta, e2, "Andrew")

    def test_conclusion(self):
        now = datetime(2031, 7, 20, tzinfo=timezone.utc)
        eid = L.new_experiment(self.cfg, self.gta, question="Q", kind="first_comment", by="Andrew", signed=True,
                               outlets=["youtube_shorts"], now=now - timedelta(days=5))
        rows = []
        with store.edit(L.experiments_path(self.gta), dict) as exps:
            for i in range(20):
                arm = "A" if i % 2 == 0 else "B"
                day = (D - timedelta(days=i)).isoformat()
                exps[eid]["posts"][arm].append({"board": day, "slot": "07:00", "practice": False})
                rows.append({"board": day, "slot": "07:00", "outlet": "youtube_shorts",
                             "rel_24h": (0.7 if arm == "B" else 0.0) + (i % 3) * 0.01})
        out = L.conclude(self.cfg, self.gta, rows, now)
        e = L.experiments(self.gta)[eid]
        self.assertEqual((e["state"], e["result"]["winner"], e["counts"]), ("done", "B", {"A": 10, "B": 10}))
        self.assertGreater(e["result"]["lift"], 1.9)
        self.assertEqual(out[0]["state"], "done")
        adv = [p for p in L.proposals(self.gta).values() if p["key"] == f"advice.experiment.{eid}"]
        self.assertEqual(len(adv), 1)
        self.assertIn("Keep arm B", adv[0]["words"])

    def test_ends_after_21_days(self):
        now = datetime(2031, 7, 30, tzinfo=timezone.utc)
        eid = L.new_experiment(self.cfg, self.gta, question="Q", kind="first_comment", by="Andrew", signed=True,
                               now=now - timedelta(days=22))
        L.conclude(self.cfg, self.gta, [], now)
        e = L.experiments(self.gta)[eid]
        self.assertEqual((e["state"], e["result"]["short"], e["result"]["winner"]), ("done", True, ""))


# ================================================================================================= the job, plugs
class JobAndPlugsTest(Base):
    def test_the_job_keeps_to_the_switch_and_the_command_does_not(self):
        self.sixty()
        self.assertEqual(L.daily(self.cfg, now=self.now), [])
        self.assertFalse(L.report_path(self.gta, D).exists())
        buf = io.StringIO()
        with mock.patch.object(self.cfg, "now", lambda: self.now), contextlib.redirect_stdout(buf):
            L.cli(self.cfg, ["daily", "--account", "gaming", "--date", D.isoformat()])
        self.assertIn("Gaming: report for 2031-07-14", buf.getvalue())
        self.assertTrue(L.report_path(self.gta, D, "md").exists())
        self.switch(self.gta, "learn", "on")
        said = L.daily(self.cfg, now=self.now)
        self.assertEqual(len(said), 1)
        self.assertIn("Gaming: report for 2031-07-14", said[0])
        self.assertEqual(SOC.run_job(self.cfg, "learn", now=self.now)[0], said[0])

    def test_everything_off_says_nothing(self):
        self.sixty()
        accts = list(self.cfg.accounts.values())
        self.assertEqual(L.summary(self.cfg, self.now, accts), [])
        self.assertEqual(L.morning_line(self.cfg, self.now), "")
        self.assertEqual(L.health(self.cfg, self.now, accts), ("", ""))
        self.assertEqual(L.card_part(self.cfg, self.gta, D, {"slot": "07:00"}, self.now), "")
        self.assertTrue(all(r[0] is None for r in L.doctor(self.cfg, self.now)))
        self.assertIn((None, "Gaming playbook: off (the scout and coach read no learned factors)", ""),
                      L.doctor(self.cfg, self.now))
        self.assertFalse((store.path("gaming", "learn")).exists())

    def test_on_says_its_chips_and_lines(self):
        self.sixty()
        self.switch(self.gta, "learn", "on")
        accts = [self.gta]
        self.assertEqual(L.health(self.cfg, self.now.replace(hour=6), accts)[0], "calm")   # not made yet
        L.daily(self.cfg, now=self.now)
        chips = L.summary(self.cfg, self.now, accts)
        words = [c["words"] for c in chips]
        self.assertTrue(words[0].startswith("Yesterday "))
        self.assertIn("problems", " ".join(words))
        self.assertTrue(all(c["href"].startswith("/reports") for c in chips))
        self.assertEqual(L.health(self.cfg, self.now.replace(hour=6), accts), ("", ""))
        with store.edit(L.proposals_path(self.gta), dict) as props:
            L._put(self.gta, {"kind": "advice", "key": "advice.x", "words": "More of this", "evidence": {"n": 9}},
                   props, datetime(2031, 7, 15, tzinfo=timezone.utc))
        self.assertEqual(L.morning_line(self.cfg, self.now), "1 proposal")
        self.assertIn("Also: 1 proposal.", SOC.morning_lines(self.cfg, self.now))
        doc = L.doctor(self.cfg, self.now)
        self.assertTrue(any(ok is True and "Gaming reports: made" in t for ok, t, _ in doc))
        self.assertIn((None, "Gaming: 60 posts with a 24h point: numbers only, no claims yet", ""), doc)
        brief = PB.brief(self.gta)
        self.assertLessEqual(len(brief.splitlines()), 40)
        self.assertIn("Biggest problems", brief)

    def test_week(self):
        self.sixty()
        for i in range(3):
            L.report(self.cfg, self.gta, day=D - timedelta(days=i), now=self.now, propose=False)
        w = L.week(self.cfg, self.gta, end=D, now=self.now)
        self.assertEqual((w["reports"], w["from"], w["to"]), (3, (D - timedelta(days=6)).isoformat(), D.isoformat()))
        self.assertEqual(w["problems"][0]["key"], "failed")
        self.assertEqual(w["posts"], 62)                                    # 60 before D and 2 on it
        json.dumps(w)



# ================================================================================================= review fixes (10.20)
class ReviewFixesTest(Base):
    def ctx(self, k: str, sha: str):
        slot = {"slot": k, "publish_at": self.cfg.slot_time(D, k).isoformat(), "status": "ready",
                "clip": {"sha256": sha}}
        return SOC.CollectCtx(cfg=self.cfg, acct=self.gta, day=D, slot=slot,
                              extras={"first_comment": {"facebook_reels": {"text": "Source: x", "from": "default"}}})

    def prop(self, prop: dict, now=datetime(2031, 7, 15, tzinfo=timezone.utc)) -> str:
        with store.edit(L.proposals_path(self.gta), dict) as props:
            return L._put(self.gta, prop, props, now)

    # a clip collected again into a slot keeps the slot's arm, and the arm is carried out on it
    def test_a_recollected_clip_keeps_its_arm(self):
        self.switch(self.gta, "learn.experiments", "on")
        self.switch(self.gta, "first_comment.facebook_reels", "on")
        eid = L.new_experiment(self.cfg, self.gta, question="First comment?", kind="first_comment", by="Andrew",
                               signed=True)
        L.on_collect(self.ctx("07:00", "s1"))
        c2 = self.ctx("10:30", "s2")
        L.on_collect(c2)
        self.assertEqual((c2.extras.get("experiment"), "first_comment" in c2.extras), ({eid: "B"}, False))
        c3 = self.ctx("10:30", "s2-new")                     # Make again: collect cleared the extras, runs again
        L.on_collect(c3)
        self.assertEqual(c3.extras.get("experiment"), {eid: "B"})
        self.assertNotIn("first_comment", c3.extras)
        posts = L.experiments(self.gta)[eid]["posts"]
        self.assertEqual([(p["slot"], p["clip_sha"]) for p in posts["B"]], [("10:30", "s2-new")])
        self.assertEqual(len(posts["A"]), 1)

    # a take-back removes that one rule; rules approved after it stay
    def test_a_take_back_removes_only_its_rule(self):
        self.switch(self.gta, "learn.playbook", "on")
        now = datetime(2031, 7, 30, 5, 0, tzinfo=timezone.utc)
        a_at, b_at = now - timedelta(days=20), now - timedelta(days=10)
        PB.apply(self.cfg, self.gta, {"id": "p1", "key": "scout.channels.mrbossftw", "to": 1.3, "name": "MrBossFTW"},
                 "Andrew", a_at)
        PB.apply(self.cfg, self.gta, {"id": "p2", "key": "coach.first_word_max_s", "to": 0.5}, "Andrew", b_at)
        rows = []
        for i, rel in enumerate((0.5, 0.6, 0.4)):
            rows.append({"pid": f"b{i}", "creator_key": "mrbossftw", "rel_24h": rel, "outlet": "youtube_shorts",
                         "went": utc(a_at - timedelta(days=i + 1))})
        for i, rel in enumerate((-0.5, -0.6, -0.4)):
            rows.append({"pid": f"a{i}", "creator_key": "mrbossftw", "rel_24h": rel, "outlet": "youtube_shorts",
                         "went": utc(a_at + timedelta(days=i + 1))})
        did = L.did_it_work(self.cfg, self.gta, rows, now)
        self.assertEqual([d["verdict"] for d in did], ["worse"])
        made = L.make_proposals(self.cfg, self.gta, [], [], now, "w", did)
        p = L.proposals(self.gta)[made[0]]
        self.assertIn("Take back \"the scout ranks MrBossFTW's videos 1.3x\"", p["words"])
        said = L.approve(self.cfg, self.gta, made[0], "Andrew", now)
        self.assertIn("took back the scout ranks MrBossFTW's videos 1.3x; 1 other rule stays", said)
        pb = PB.read(self.gta, now)
        self.assertEqual([r["key"] for r in pb["rules"]], ["coach.first_word_max_s"])
        self.assertEqual((pb["version"], pb["coach"]["first_word_max_s"], pb["scout"]["channels"]), (3, 0.5, {}))
        self.assertEqual(PB.history(self.gta)[-1]["what"], "take back")
        with self.assertRaises(SystemExit):                  # it is gone: nothing more to take back
            PB.take_back(self.cfg, self.gta, did[0]["rule"], "Andrew", now)

    # an experiment counts clips, not one post per platform
    def test_an_experiment_counts_clips(self):
        self.switch(self.gta, "learn.experiments", "on")
        self.switch(self.gta, "first_comment.facebook_reels", "on")
        eid = L.new_experiment(self.cfg, self.gta, question="First comment?", kind="first_comment", by="Andrew",
                               signed=True, now=datetime(2031, 7, 14, tzinfo=timezone.utc))
        slots = [f"{h:02d}:00" for h in range(5, 25)][:20]
        rows = []
        for i, k in enumerate(slots):
            if k == "24:00":
                k = "04:30"
            L.on_collect(self.ctx(k, "x" + k))
            for o in ("youtube_shorts", "facebook_reels", "instagram_reels"):
                rows.append({"board": D.isoformat(), "slot": k, "outlet": o, "rel_24h": 0.1})
            if i == 7:
                L.conclude(self.cfg, self.gta, rows, datetime(2031, 7, 16, tzinfo=timezone.utc))
                e = L.experiments(self.gta)[eid]
                self.assertEqual((e["state"], e["counts"]), ("running", {"A": 4, "B": 4}))   # 12 rows an arm
        L.conclude(self.cfg, self.gta, rows, datetime(2031, 7, 16, tzinfo=timezone.utc))
        e = L.experiments(self.gta)[eid]
        self.assertEqual((e["state"], e["result"]["n_a"], e["result"]["n_b"]), ("done", 10, 10))
        self.assertIn("over 10 and 10 clips", e["result"]["words"])

    # Test it first is refused for a change that cannot be split clip by clip
    def test_test_it_first_is_refused_for_a_scout_factor(self):
        pid = self.prop({"kind": "factor", "key": "scout.channels.mrbossftw", "from": 1.0, "to": 1.26,
                         "words": "The scout ranks MrBossFTW's videos 1.26x", "evidence": {"n": 20}})
        for p in ({"key": "scout.channels.x"}, {"key": "coach.first_word_max_s"}, {"key": "runbook.x"},
                  {"key": "draft_rule.asks_source"}, {"key": "advice.hour.x"}):
            self.assertIsNone(L.experiment_for(p))
        with self.assertRaises(SystemExit) as cm:
            L.decline(self.cfg, self.gta, pid, "Andrew", "test_first", now=datetime(2031, 7, 15, tzinfo=timezone.utc))
        self.assertIn("cannot be split clip by clip", str(cm.exception))
        self.assertEqual((L.proposals(self.gta)[pid]["state"], L.experiments(self.gta)), ("open", {}))
        with self.assertRaises(SystemExit):                  # nor can one be made by hand
            L.new_experiment(self.cfg, self.gta, question="Q", kind="tag", by="Andrew")

    def test_an_old_label_only_experiment_never_names_a_keeper(self):
        now = datetime(2031, 7, 20, tzinfo=timezone.utc)
        with store.edit(L.experiments_path(self.gta), dict) as exps:
            exps["e-0001"] = {"id": "e-0001", "question": "Q", "kind": "tag", "arms": {"A": "as now", "B": "x"},
                              "assign": "alternate", "outlets": [], "min_n": 10, "max_days": 21, "state": "running",
                              "posts": {"A": [], "B": []}, "signed": {"by": "Andrew"}, "started": utc(now)}
        rows = []
        with store.edit(L.experiments_path(self.gta), dict) as exps:
            for i in range(20):
                arm = "AB"[i % 2]
                day = (D - timedelta(days=i)).isoformat()
                exps["e-0001"]["posts"][arm].append({"board": day, "slot": "07:00"})
                rows.append({"board": day, "slot": "07:00", "outlet": "youtube_shorts",
                             "rel_24h": 0.7 if arm == "B" else 0.0})
        L.conclude(self.cfg, self.gta, rows, now)
        self.assertEqual(L.experiments(self.gta)["e-0001"]["result"]["winner"], "B")
        self.assertEqual(L.proposals(self.gta), {})

    # a draft automation is noted only, and says so; handed over when the Automations part offers draft_from
    def test_a_draft_rule_says_where_it_goes(self):
        to = {"intent": "asks_source", "n": 9, "words": "where is this from"}
        pid = self.prop({"kind": "draft_rule", "key": "draft_rule.asks_source", "from": None, "to": to,
                         "words": "Draft an automation", "evidence": {"metric": "unanswered", "n": 9}})
        real = L.REG.fn
        with mock.patch.object(L.REG, "fn", lambda spec: None if spec == L.DRAFT_FN else real(spec)):  # not built
            said = L.approve(self.cfg, self.gta, pid, "Andrew", datetime(2031, 7, 15, tzinfo=timezone.utc))
            self.assertIn("noted only", L.draft_words(self.gta))
        self.assertIn("a request for an automation that answers \"where is this from\"", said)
        self.assertIn("nothing is drafted or sent by this: make the rule on Inbox, Automations", said)
        self.assertNotIn("draft", PB.brief(self.gta).lower().replace("drafted", ""))     # not runbook guidance
        got = []

        def draft_from(cfg, acct, intent, words, by, now):
            got.append((acct.slug, intent, words, by))
            return "Gaming: draft rule where-is-this-from added in practice, unsigned"
        with mock.patch.object(L.REG, "fn", lambda spec: draft_from if spec == L.DRAFT_FN else real(spec)):
            pid2 = self.prop({"kind": "draft_rule", "key": "draft_rule.asks_who", "from": None,
                              "to": {"intent": "asks_who", "n": 7, "words": "who is that"}, "words": "Draft",
                              "evidence": {"metric": "unanswered", "n": 7}})
            said = L.approve(self.cfg, self.gta, pid2, "Andrew", datetime(2031, 7, 15, tzinfo=timezone.utc))
            self.assertIn("unsigned draft in practice", L.draft_words(self.gta))
        self.assertEqual(got, [("gaming", "asks_who", "who is that", "Andrew")])
        self.assertIn("added in practice, unsigned", said)
        # with the automations part built (2026-10-20): the real rules.draft_from makes the starter answer a rule,
        # in practice and unsigned; a second request for the same question drafts nothing new
        from clipfactory import rules as RU
        pid3 = self.prop({"kind": "draft_rule", "key": "draft_rule.asks_full", "from": None,
                          "to": {"intent": "asks_full", "n": 6, "words": "full video"}, "words": "Draft",
                          "evidence": {"metric": "unanswered", "n": 6}})
        said = L.approve(self.cfg, self.gta, pid3, "Andrew", datetime(2031, 7, 15, tzinfo=timezone.utc))
        self.assertIn("drafted on Inbox, Automations, in practice and unsigned", said)
        made = [r for r in RU.rules(self.gta) if r.get("from") == "learn"]
        self.assertEqual([(r["mode"], r["signoff"], "asks_full" in r["trigger"]["intents"]) for r in made],
                         [("practice", None, True)])
        self.assertIn("already answers", RU.draft_from(self.cfg, self.gta, "asks_full", "", "Andrew"))
        with self.assertRaises(SystemExit):
            RU.draft_from(self.cfg, self.gta, "sing_a_song", "", "Andrew")

    # did it work waits for the playbook switch, and measures what it can
    def test_did_it_work_waits_for_the_switch(self):
        t0 = datetime(2031, 6, 1, 12, tzinfo=timezone.utc)
        PB.apply(self.cfg, self.gta, {"key": "scout.channels.mrbossftw", "to": 1.3}, "Andrew", t0)
        rows = (rows_of([0.5] * 5, start=t0 - timedelta(days=6), creator_key="mrbossftw")
                + rows_of([-0.5] * 5, start=t0 + timedelta(days=1), creator_key="mrbossftw"))
        for i, r in enumerate(rows):
            r["pid"] = f"d{i}"
        now = t0 + timedelta(days=20)
        did = L.did_it_work(self.cfg, self.gta, rows, now)
        self.assertEqual([d["verdict"] for d in did], [L.NOT_IN_FORCE])                # off all along
        self.assertEqual(L.make_proposals(self.cfg, self.gta, [], [], now, "w", did), [])
        self.assertEqual(L.did_it_work_of(self.gta), {})                                # asked again later
        on_at = t0 + timedelta(days=10)                      # switched on 10 days after the approval
        SW_ = __import__("clipfactory.switches", fromlist=["x"])
        SW_.set_mode(self.cfg, self.gta, "learn.playbook", "on", "Andrew", now=on_at)
        self.assertEqual(L.did_it_work(self.cfg, self.gta, rows, t0 + timedelta(days=20)), [])   # not 14 days yet
        late = (rows_of([-0.6] * 4, start=on_at + timedelta(days=1), creator_key="mrbossftw"))
        for i, r in enumerate(late):
            r["pid"] = f"l{i}"
        did = L.did_it_work(self.cfg, self.gta, rows + late, on_at + timedelta(days=15))
        self.assertEqual(did[0]["in_force_from"], utc(on_at))
        self.assertEqual((did[0]["before"]["n"], did[0]["after"]["n"]), (8, 4))       # measured from the switch

    def test_did_it_work_reads_coach_marks_and_says_not_measurable(self):
        self.switch(self.gta, "learn.playbook", "on")
        t0 = datetime(2031, 6, 20, 12, tzinfo=timezone.utc)
        PB.apply(self.cfg, self.gta, {"key": "coach.prefer.x", "to": "a number in the title"}, "Andrew", t0)
        PB.apply(self.cfg, self.gta, {"key": "runbook.x", "to": "Lead with the answer."}, "Andrew", t0)
        num = {"number": True, "question": False, "quote": False, "name": False, "caps_ratio": 0}
        rows = (rows_of([0.5] * 8, start=t0 - timedelta(days=9), title_feat=num)
                + rows_of([-0.5] * 8, start=t0 + timedelta(days=1), title_feat=num)
                + rows_of([0.9] * 8, start=t0 + timedelta(days=1), title_feat=dict(num, number=False)))
        for i, r in enumerate(rows):
            r["pid"] = f"c{i}"
        did = {d["key"]: d for d in L.did_it_work(self.cfg, self.gta, rows, t0 + timedelta(days=20))}
        self.assertEqual(did["coach.prefer.a-number-in-the-title"]["verdict"], "worse")
        self.assertEqual(did["coach.prefer.a-number-in-the-title"]["after"]["n"], 8)
        self.assertEqual(did["runbook.lead-with-the-answer"]["verdict"], L.NOT_MEASURABLE)

    # an approved change blocks the same proposal only while it stands
    def test_proposed_again_once_taken_back(self):
        t = datetime(2031, 7, 15, tzinfo=timezone.utc)
        prop = dict(L._from_work(self.gta, work(), []), evidence={"n": 24})
        pid = self.prop(dict(prop), t)
        L.approve(self.cfg, self.gta, pid, "Andrew", t)
        self.assertIsNone(self.prop(dict(prop), t + timedelta(days=1)))                  # it stands
        PB.revert(self.cfg, self.gta, 0, "Andrew", t + timedelta(days=2))
        self.assertTrue(self.prop(dict(prop), t + timedelta(days=3)))                    # gone: may come again
        adv = {"kind": "advice", "key": "advice.hour.x", "words": "More at 07:00", "evidence": {"n": 20}}
        a1 = self.prop(dict(adv), t)
        L.approve(self.cfg, self.gta, a1, "Andrew", t)
        self.assertIsNone(self.prop(dict(adv), t + timedelta(days=5)))                   # advice: 60 days

    # the runbooks read nothing while the playbook switch is off; what works names its platform
    def test_the_brief_keeps_to_the_switch(self):
        t = datetime(2031, 7, 15, tzinfo=timezone.utc)
        pid = self.prop({"kind": "runbook_note", "key": "runbook.x", "to": "Never pick a clip under 30 seconds.",
                         "words": "Never pick a clip under 30 seconds.", "evidence": {"n": 0}}, t)
        L.approve(self.cfg, self.gta, pid, "Andrew", t)
        store.write(L.report_path(self.gta, D), {"day": D.isoformat(), "works": [
            dict(work("format", "gaggle", 0.7, "facebook_reels", "likely")),
            dict(work("format", "gaggle", 0.7, "instagram_reels", "early sign"))], "problems": [], "best": []})
        off = PB.brief(self.gta)
        self.assertNotIn("Never pick a clip", off)
        self.assertNotIn("Runbook notes", off)
        self.assertIn("The playbook switch is off: nothing in it is in force (1 approved rule waits", off)
        self.assertIn("- the gaggle format on Facebook: 0.7x the usual (likely)", off)
        self.assertNotIn("Instagram", off)
        self.assertIn("Not proven yet: 1 early sign", off)
        self.switch(self.gta, "learn.playbook", "on")
        on = PB.brief(self.gta)
        self.assertIn("Runbook notes:\n- Never pick a clip under 30 seconds.", on)

    # the Best chip names the creator, or the title, never a slug's first word
    def test_the_best_chip_names_who_or_what(self):
        self.switch(self.gta, "learn", "on")
        y = (self.now.date() - timedelta(days=1))
        best = {"slug": "the-thing-has-completely-crashed", "title": "The thing has completely crashed on stream",
                "host": "", "views_24h": 19177, "outlet": "youtube_shorts"}
        store.write(L.report_path(self.gta, y), {"day": y.isoformat(), "kpis": {"views_day": 900}, "best": [best]})
        words = [c["words"] for c in L.summary(self.cfg, self.now, [self.gta])]
        self.assertIn('Best: "The thing has completely crashed..." 19.2K on YouTube', words)
        best["host"] = "MrBossFTW"
        store.write(L.report_path(self.gta, y), {"day": y.isoformat(), "kpis": {"views_day": 900}, "best": [best]})
        self.assertIn("Best: MrBossFTW 19.2K on YouTube", [c["words"] for c in L.summary(self.cfg, self.now,
                                                                                          [self.gta])])

    # names keep their capitals
    def test_words_keep_their_capitals(self):
        self.assertEqual(L.upper_first("clips from C-SPAN"), "Clips from C-SPAN")
        self.assertEqual(L.upper_first("posts on Monday"), "Posts on Monday")
        rep = {"day": D.isoformat(), "name": "Gaming", "kpis": {"posts_day": 0, "views_day": 0, "slots_planned": 0,
                                                                  "slots_signed": 0, "views_lost": 0},
               "problems": [], "best": [], "worst": [], "coach_accuracy": {}, "health": {},
               "works": [dict(work("creator", "c-span", 0.7, "facebook_reels", "likely"),
                              words="clips from C-SPAN")]}
        self.assertIn("- Clips from C-SPAN on Facebook", L.markdown(rep))
        prop = L._from_work(self.gta, dict(work(), words="clips from MrBossFTW"), [])
        PB.apply(self.cfg, self.gta, dict(prop, id="p"), "Andrew")
        self.assertEqual(PB.rule_words(PB.read(self.gta)["rules"][0]), "the scout ranks MrBossFTW's videos 1.3x")

    # why chips: proven findings only, nothing said twice; slow starts are not added into views lost
    def test_why_chips_and_slow_starts(self):
        r = {"outlet": "facebook_reels", "rel_24h": math.log(0.5), "format": "gaggle", "host": "C-SPAN",
             "creator_key": "c-span", "late_min": 0}
        works = [dict(work("format", "gaggle", 0.7, "facebook_reels", "likely")),
                 dict(work("creator", "c-span", 0.7, "facebook_reels", "early sign"), words="clips from C-SPAN")]
        why = L._why(r, works, best=False)
        self.assertEqual(why, ["0.5x the usual", "the gaggle format: 0.7x", "from C-SPAN"])
        noon = datetime(2031, 7, 14, 12, tzinfo=self.cfg.tz())
        rows = [{"pid": f"s{i}", "outlet": "youtube_shorts", "board": D.isoformat(), "slot": "07:00",
                 "went": utc(noon), "rel_24h": math.log(0.3), "views": {"24h": 30}, "baseline_24h": 100.0,
                 "late_min": 0} for i in range(3)]
        probs = L.problems(self.cfg, self.gta, rows, [], D, self.now, [], (D - timedelta(days=1), D))
        slow = [p for p in probs if p["key"] == "slow_start"][0]
        self.assertEqual((slow["overlaps"], slow["views_lost"]), (True, 210))


# ================================================================================================= review fixes (10.20, 2)
class SecondReviewTest(Base):
    def hist(self, s: dict, outlet: str, pairs) -> None:
        """Stats snapshots of one post: (hours after it went up, views)."""
        went = datetime.fromisoformat(s["publish_at"])
        pid = s["outlets"][outlet]["platform_id"]
        sid = ST.post_id({"account": "gaming", "platform": L.DASH[outlet], "pid": pid})
        p = store.path("stats", "history", f"{went.date()}.json")
        data = store.read(p, {}) or {"day": str(went.date()), "reads": []}
        for h, v in pairs:
            data["reads"].append({"at": utc(went + timedelta(hours=h)),
                                  "posts": {sid: [v, 0, 0, 0, int(went.timestamp())]}})
        store.write(p, data)

    # two slots holding one platform id (the nightly read matched a sibling's post) make one row, under the slot that
    # posted it, as checkpoints reads it
    def test_one_post_held_by_two_slots_is_one_row(self):
        day = D - timedelta(days=2)
        a = self.slot(day, "07:00", fmt="room", host="C-SPAN")
        self.post(a, "facebook_reels", 900)
        b = self.slot(day, "10:30", fmt="gaggle", host="MrBossFTW")
        b["outlets"]["facebook_reels"] = dict(a["outlets"]["facebook_reels"], provider="matched")
        self.board(day, {"07:00": a, "10:30": b})
        rows = L.rows_for(self.cfg, self.gta, self.now)
        self.assertEqual([(r["slot"], r["format"], r["views"].get("24h")) for r in rows], [("07:00", "room", 900)])
        a["outlets"]["facebook_reels"]["provider"] = "matched"            # the matched copy first in the day:
        b["outlets"]["facebook_reels"]["provider"] = "direct"             # the one that posted it still wins
        self.board(day, {"07:00": a, "10:30": b})
        rows = L.rows_for(self.cfg, self.gta, self.now)
        self.assertEqual([(r["slot"], r["format"]) for r in rows], [("10:30", "gaggle")])

    # the Facebook cap test (2026-10-27): a post the poster marked past the usual daily cap is left out (Facebook
    # barely shows those, so its numbers say nothing about the clip); the clip's other platforms still count
    def test_a_cap_test_post_past_the_usual_cap_is_left_out(self):
        day = D - timedelta(days=2)
        a = self.slot(day, "07:00")
        self.post(a, "facebook_reels", 900)
        b = self.slot(day, "10:30")
        self.post(b, "facebook_reels", 4)
        self.post(b, "instagram_reels", 300)
        b["outlets"]["facebook_reels"]["past_usual_cap"] = 6
        self.board(day, {"07:00": a, "10:30": b})
        rows = L.rows_for(self.cfg, self.gta, self.now)
        self.assertEqual(sorted((r["slot"], r["outlet"]) for r in rows),
                         [("07:00", "facebook_reels"), ("10:30", "instagram_reels")])

    # a snapshot of a post no read has reached holds a 0 that was never read: no 24-hour number from it; nor from a
    # snapshot after the post's last read (a copy of that read)
    def test_snapshots_are_trusted_only_up_to_the_last_read(self):
        never = self.slot(D - timedelta(days=3), "07:00")
        self.post(never, "facebook_reels", None)
        never["outlets"]["facebook_reels"].pop("metrics")
        self.board(D - timedelta(days=3), {"07:00": never})
        self.hist(never, "facebook_reels", [(20, 0), (44, 0)])
        late = self.slot(D - timedelta(days=4), "07:00")                  # first read at 28 h (480)
        self.post(late, "facebook_reels", None)
        went = datetime.fromisoformat(late["publish_at"])
        late["outlets"]["facebook_reels"]["metrics"] = {"views": 480, "at": utc(went + timedelta(hours=28))}
        self.board(D - timedelta(days=4), {"07:00": late})
        self.hist(late, "facebook_reels", [(20, 0), (40, 600)])
        stale = self.slot(D - timedelta(days=5), "07:00")                 # last read at 22 h (420), then copies
        self.post(stale, "facebook_reels", None)
        went = datetime.fromisoformat(stale["publish_at"])
        stale["outlets"]["facebook_reels"]["metrics"] = {"views": 420, "at": utc(went + timedelta(hours=22))}
        self.board(D - timedelta(days=5), {"07:00": stale})
        self.hist(stale, "facebook_reels", [(20, 400), (26, 420)])
        rows = {r["board"]: r for r in L.rows_for(self.cfg, self.gta, self.now)}
        r = rows[(D - timedelta(days=3)).isoformat()]
        self.assertEqual((r["views"].get("24h"), r["v24_src"]), (None, ""))
        L.add_baselines(list(rows.values()))
        self.assertIsNone(r["rel_24h"])
        r = rows[(D - timedelta(days=4)).isoformat()]
        self.assertEqual((r["views"]["24h"], r["v24_src"]), (round(480 * math.log1p(12) / math.log1p(14)), "estimate"))
        r = rows[(D - timedelta(days=5)).isoformat()]
        self.assertEqual(r["v24_src"], "estimate")
        self.assertGreater(r["views"]["24h"], 420)

    # did it work counts clips: one clip before and one after on three platforms is too few to tell
    def test_did_it_work_counts_clips_not_platforms(self):
        self.switch(self.gta, "learn.playbook", "on")
        t0 = datetime(2031, 6, 20, 12, tzinfo=timezone.utc)
        PB.apply(self.cfg, self.gta, {"key": "scout.channels.mrbossftw", "to": 1.3, "id": "p-x"}, "Andrew", t0)

        def clip(day_off, rel):
            out = [r for o in BASE for r in rows_of([rel], o, start=t0 + timedelta(days=day_off),
                                                    creator_key="mrbossftw")]
            for r in out:
                r["pid"] = f"{day_off}-{r['outlet']}"
            return out
        now = t0 + timedelta(days=20)
        did = L.did_it_work(self.cfg, self.gta, clip(-5, 0.5) + clip(5, -0.5), now)
        self.assertEqual((did[0]["verdict"], did[0]["before"]["n"], did[0]["after"]["n"]),
                         ("too few posts to tell", 1, 1))
        self.assertEqual(L.make_proposals(self.cfg, self.gta, [], [], now, "w", did), [])
        L.did_path(self.gta).unlink()
        rows = [r for d in (-6, -5, -4) for r in clip(d, 0.5)] + [r for d in (4, 5, 6) for r in clip(d, -0.5)]
        did = L.did_it_work(self.cfg, self.gta, rows, now)
        self.assertEqual((did[0]["verdict"], did[0]["before"]["n"], did[0]["after"]["n"]), ("worse", 3, 3))

    # an experiment that runs out of days short of clips names no winner and files no keeper
    def test_a_late_experiment_short_of_clips_names_no_winner(self):
        now = datetime(2031, 7, 30, tzinfo=timezone.utc)
        eid = L.new_experiment(self.cfg, self.gta, question="Q", kind="first_comment", by="Andrew", signed=True,
                               outlets=["youtube_shorts"], now=now - timedelta(days=22))
        rows = []
        with store.edit(L.experiments_path(self.gta), dict) as exps:
            for i in range(6):
                arm = "AB"[i % 2]
                day = (D - timedelta(days=i)).isoformat()
                exps[eid]["posts"][arm].append({"board": day, "slot": "07:00", "practice": False})
                rows.append({"board": day, "slot": "07:00", "outlet": "youtube_shorts",
                             "rel_24h": (0.7 if arm == "B" else 0.0) + i * 0.01})
        L.conclude(self.cfg, self.gta, rows, now)
        res = L.experiments(self.gta)[eid]["result"]
        self.assertEqual((res["winner"], res["short"], res["n_a"], res["n_b"]), ("", True, 3, 3))
        self.assertIn("ended short: no clear winner", res["words"])
        self.assertEqual(L.proposals(self.gta), {})

    # findings on different platforms about one account-wide setting: proposed together when they agree, not at all
    # when they disagree, and an open proposal is never given another platform's opposite evidence
    def test_platforms_that_disagree_make_no_account_wide_proposal(self):
        t = datetime(2031, 7, 15, tzinfo=timezone.utc)
        fb = dict(n=14, conf="likely", outlet="facebook_reels")
        works = [work(lift=1.8), work(lift=0.55, **fb), work("title_number", True, 1.6),
                 work("title_number", True, 0.6, **fb)]
        self.assertEqual(L.make_proposals(self.cfg, self.gta, works, [], t, "w", []), [])
        self.assertEqual(L.proposals(self.gta), {})
        made = L.make_proposals(self.cfg, self.gta, [work(lift=1.8), work(lift=1.3, **fb)], [], t, "w", [])
        self.assertEqual(len(made), 1)
        p = L.proposals(self.gta)[made[0]]
        self.assertIn("1.8x the usual on YouTube, 1.3x on Facebook", p["words"])
        self.assertEqual((p["evidence"]["outlet"], [a["outlet"] for a in p["evidence"]["also"]]),
                         ("youtube_shorts", ["facebook_reels"]))
        before = json.dumps(p, sort_keys=True)
        self.assertEqual(L.make_proposals(self.cfg, self.gta, [work(lift=0.55, **fb)], [], t + timedelta(days=1), "w",
                                          []), [])
        self.assertEqual(json.dumps(L.proposals(self.gta)[made[0]], sort_keys=True), before)   # left as it was
        pref = L.make_proposals(self.cfg, self.gta, [work("title_number", True, 1.6)], [], t, "w", [])
        self.assertEqual(L.proposals(self.gta)[pref[0]]["key"], "coach.prefer.a-number-in-the-title")
        self.assertEqual(L.make_proposals(self.cfg, self.gta, [work("title_number", True, 0.6, **fb)], [],
                                          t + timedelta(days=1), "w", []), [])          # no opposite rule beside it
        self.assertEqual(len(L.proposals(self.gta)), 2)

    # the coach's accuracy counts clips: five clips on three platforms are too few for a range
    def test_coach_accuracy_counts_clips(self):
        def rows(n):
            return [{"board": f"2031-06-{i + 1:02d}", "slot": "07:00", "outlet": o, "rel_24h": i * 0.1 + j * 0.01,
                     "coach": {"measured": 50 + i, "written": None}} for i in range(n) for j, o in enumerate(BASE)]
        got = L.coach_accuracy(rows(5))
        self.assertEqual((got["measured"]["n"], got["measured"]["ci90"]), (5, None))
        self.assertEqual(got["words"], "Too few scored clips to tell yet (5).")
        got = L.coach_accuracy(rows(15))
        self.assertEqual(got["measured"]["n"], 15)
        self.assertIsNotNone(got["measured"]["ci90"])
        self.assertIn("over 15 clips", got["words"])

    # a first-comment test waits until that platform's first comment is on: in practice nothing is posted in either arm
    def test_a_first_comment_test_waits_for_the_first_comment_switch(self):
        self.switch(self.gta, "learn.experiments", "on")
        self.switch(self.gta, "first_comment.facebook_reels", "practice")
        eid = L.new_experiment(self.cfg, self.gta, question="Q", kind="first_comment", by="Andrew", signed=True,
                               outlets=["facebook_reels"])

        def ctx(k):
            return SOC.CollectCtx(cfg=self.cfg, acct=self.gta, day=D, slot=self.slot(D, k), extras={
                "first_comment": {"facebook_reels": {"text": "Full video", "from": "default"}}})
        c = ctx("07:00")
        L.on_collect(c)
        a = L.experiments(self.gta)[eid]["posts"]["A"][0]
        self.assertEqual((a["practice"], a["why"]), (True, "it waits for the first comment switch"))
        self.assertNotIn("experiment", c.extras)
        self.switch(self.gta, "first_comment.facebook_reels", "on")
        c = ctx("10:30")
        L.on_collect(c)
        self.assertIn(eid, c.extras["experiment"])
        self.assertFalse(L.experiments(self.gta)[eid]["posts"]["B"][0]["practice"])

    # "a first comment" is what was posted: done is yes, none signed is no, practice or not yet is not known
    def test_the_first_comment_factor_reads_what_was_posted(self):
        day = D - timedelta(days=2)
        s = self.slot(day, "07:00")
        for o in BASE:
            self.post(s, o, 100)
        s["outlets"]["facebook_reels"]["social"] = {"first_comment": {"state": "practice"}}
        s["outlets"]["instagram_reels"]["social"] = {"first_comment": {"state": "done"}}
        self.board(day, {"07:00": s})
        ex = {"first_comment": {"facebook_reels": {"text": "x"}, "instagram_reels": {"text": "x"}}}
        with mock.patch.object(L, "_extras", lambda slot: ex):
            got = {r["outlet"]: r["extras"]["first_comment"] for r in L.rows_for(self.cfg, self.gta, self.now)}
        self.assertEqual(got, {"facebook_reels": None, "instagram_reels": True, "youtube_shorts": False})


# ================================================================================ the usual, drops and gaps (10.20, 3)
def vrows(vals, outlet="youtube_shorts", start=datetime(2031, 7, 6, 12, tzinfo=timezone.utc), **extra):
    """Rows with these 24-hour views, one a day from 07.06 (06:00 local), as rows_for makes them."""
    out = []
    for i, v in enumerate(vals):
        t = start + timedelta(days=i)
        row = {"pid": f"{outlet[:2]}{i}", "outlet": outlet, "board": t.date().isoformat(), "slot": "07:00",
               "went": utc(t), "views": {"24h": v}, "sent_s": 40.0, "cut": False, "title_feat": {"chars": 50},
               "late_min": 0.0}
        row.update(extra)
        out.append(row)
    return out


def mini_rep(probs, **kw) -> dict:
    rep = {"day": D.isoformat(), "name": "Gaming", "kpis": {"posts_day": 0, "views_day": 0, "slots_planned": 0,
                                                              "slots_signed": 0, "views_lost": 0},
           "problems": probs, "best": [], "worst": [], "works": [], "coach_accuracy": {}, "health": {}}
    rep.update(kw)
    return rep


class UsualDropGapTest(Base):
    # 1. a usual needs five earlier posts on the same platform; until then a post is in no claim, and the report
    # says so in plain words
    def politics_like(self) -> None:
        """D-2 and D-1, four slots each: YouTube and Facebook every slot, Instagram on D-2 only (4 posts); D: one
        slot with no clip. YouTube's first five (9 3 640 1258 7426) have no usual; then 2, 1599 and 5."""
        yt = [9, 3, 640, 1258, 7426, 2, 1599, 5]
        fb = [240, 260, 250, 255, 245, 251, 252, 248]
        ig = [185, 147, 268, 173]
        i = 0
        for day in (D - timedelta(days=2), D - timedelta(days=1)):
            slots = {}
            for k in ("07:00", "08:45", "10:30", "12:15"):
                s = self.slot(day, k)
                self.post(s, "youtube_shorts", yt[i])
                self.post(s, "facebook_reels", fb[i])
                if i < 4:
                    self.post(s, "instagram_reels", ig[i])
                slots[k] = s
                i += 1
            self.board(day, slots)
        self.board(D, {"07:00": self.slot(D, "07:00", status="open")})

    def test_no_usual_keeps_a_post_out_of_every_claim(self):
        self.politics_like()
        rep = L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        md = L.report_path(self.gta, D, "md").read_text()
        rows = {(r["outlet"], r["views"]["24h"]): r for r in L.rows_for(self.cfg, self.gta, self.now)}
        L.add_baselines(list(rows.values()))
        firsts = [("youtube_shorts", v) for v in (9, 3, 640, 1258, 7426)]
        self.assertEqual({rows[k]["rel_24h"] for k in firsts}, {None})
        named = {(b["outlet"], b["views_24h"]) for b in rep["best"] + rep["worst"]}
        self.assertEqual(named, {("youtube_shorts", 1599), ("youtube_shorts", 2), ("youtube_shorts", 5)})
        self.assertEqual([b["baseline_24h"] for b in rep["worst"]], [640, 640])     # 2 and 5 against the first five
        self.assertEqual(rep["best"][0]["baseline_24h"], 324.5)                     # 9 3 640 1258 7426 2
        slow = [p for p in rep["problems"] if p["key"] == "slow_start"][0]
        self.assertEqual((slow["n"], slow["views_lost"]), (2, (640 - 2) + (640 - 5)))
        self.assertNotIn("instagram_reels", {e["outlet"] for p in rep["problems"] for e in p["evidence"]
                                             if e.get("outlet")})
        self.assertIn("No usual yet on Instagram: 4 posts with a 24-hour number so far, and it takes 5.", md)
        self.assertNotIn("No usual yet on YouTube", md)
        self.assertIn("Left out of best and worst: 2 posts with fewer than 5 earlier posts on their platform to "
                      "compare with (Facebook 1, YouTube 1).", md)
        empty = [p for p in rep["problems"] if p["key"] == "empty"][0]
        at = self.cfg.slot_time(D, "07:00")
        want = round(L.baseline_for(list(rows.values()), "youtube_shorts", at)[0]
                     + L.baseline_for(list(rows.values()), "facebook_reels", at)[0])
        self.assertEqual((empty["views_lost"], empty["unknown"]), (want, ["instagram_reels"]))
        self.assertIn(f"1 slot had no clip by its time: about {want:,} views lost, not counting Instagram (no usual "
                      f"yet there).", md)
        self.assertEqual(rep["kpis"]["views_lost"], want)
        for bad in ("—", "–", "!"):
            self.assertNotIn(bad, md)

    def test_a_late_post_with_no_usual_says_so(self):
        p = {"key": "late", "kind": "late", "n": 2, "views_lost": 0, "unknown": ["instagram_reels"],
             "evidence": [], "words": "2 posts went up late (up to 6.3 hours)", "fix": "Sign."}
        self.assertEqual(L.lost_words(p), "views lost not known (no usual yet on Instagram)")
        self.assertIn("1. 2 posts went up late (up to 6.3 hours): views lost not known (no usual yet on Instagram).",
                      L.markdown(mini_rep([p])))

    # 2. a sudden drop on a platform is a problem, ranked by its views lost, with the dates and what to check
    def test_a_sudden_drop(self):
        vals = [1000, 1040, 1100, 990, 1050, 6, 5, 7, 8, 6]
        i = 0
        for back in range(5, 0, -1):
            day = D - timedelta(days=back)
            slots = {}
            for k in ("07:00", "19:15"):
                s = self.slot(day, k)
                self.post(s, "youtube_shorts", vals[i])
                slots[k] = s
                i += 1
            self.board(day, slots)
        self.board(D, {"07:00": self.slot(D, "07:00", status="open")})
        rep = L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        md = L.report_path(self.gta, D, "md").read_text()
        self.assertEqual([p["key"] for p in rep["problems"]], ["drop:youtube_shorts", "empty"])   # by views lost
        drop = rep["problems"][0]
        self.assertEqual((drop["n"], drop["views_lost"], drop["overlaps"]), (5, 5 * 1040 - (6 + 5 + 7 + 8 + 6), True))
        self.assertEqual(drop["words"], "YouTube views dropped: the last 5 posts (07.11 to 07.13) got 6 at 24 hours, "
                                        "the 5 before (07.09 to 07.11) got 1,040")
        for part in ("a copyright claim or a limited video in YouTube Studio", "the upload cut, the title and the "
                     "posting time", "The last 5 against the 5 before:", "own cut on 0 of 5 against 0 of 5",
                     "posted about 19:00 against 07:00"):
            self.assertIn(part, drop["fix"])
        self.assertEqual([p for p in rep["problems"] if p["key"] == "slow_start"], [])    # not said twice
        self.assertIn("1. YouTube views dropped: the last 5 posts (07.11 to 07.13) got 6 at 24 hours, the 5 before "
                      "(07.09 to 07.11) got 1,040: about 5,168 views lost.", md)
        self.assertIn("Which: 07.11 19:15 YouTube, 07.12 07:00 YouTube, 07.12 19:15 YouTube, 07.13 07:00 YouTube, "
                      "07.13 19:15 YouTube.", md)
        self.assertEqual(rep["kpis"]["views_lost"], rep["problems"][1]["views_lost"])     # the day's own only
        self.assertIn("The drops below are kept out of this total", md)
        self.switch(self.gta, "learn", "on")
        store.write(L.report_path(self.gta, self.now.date() - timedelta(days=1)), rep)
        self.assertIn("2 problems", [c["words"] for c in L.summary(self.cfg, self.now, [self.gta])])

    def test_what_is_not_a_drop(self):
        win = (D - timedelta(days=1), D)
        self.assertEqual(L.drops(self.cfg, vrows([1000] * 4 + [6] * 3), win), [])          # 3 a side: too few
        self.assertEqual(L.drops(self.cfg, vrows([300] * 4 + [100] * 4), win), [])         # a third is not under
        got = L.drops(self.cfg, vrows([300] * 4 + [99] * 4), win)
        self.assertEqual([(d["n"], d["median_last"], d["median_before"]) for d in got], [(4, 99, 300)])
        self.assertEqual(L.drops(self.cfg, vrows([300] * 4 + [99] * 4), (D + timedelta(days=5),) * 2), [])  # old
        pooled = vrows([1000] * 8) + vrows([10] * 8, "facebook_reels", start=datetime(2031, 7, 6, 13,
                                                                                    tzinfo=timezone.utc))
        self.assertEqual(L.drops(self.cfg, pooled, win), [])                              # never across platforms
        n9 = L.drops(self.cfg, vrows([500] * 5 + [5] * 5, start=datetime(2031, 7, 5, 12, tzinfo=timezone.utc)),
                     win)
        self.assertEqual([d["n"] for d in n9], [5])

    # 3. a platform far behind the account's best is a problem, with the numbers on both sides
    def test_a_platform_gap(self):
        rows = (vrows([13] * 8) + vrows([253] * 8, "facebook_reels", sent_s=30.0, cut=True, title_feat={"chars": 80})
                + vrows([150] * 8, "instagram_reels"))
        probs = L.problems(self.cfg, self.gta, rows, [], D, self.now, [], (D - timedelta(days=1), D))
        gap = [p for p in probs if p["kind"] == "gap"]
        self.assertEqual([p["key"] for p in gap], ["gap:youtube_shorts"])
        self.assertEqual(gap[0]["words"], "YouTube is Gaming's weakest platform: 13 views is usual there, 253 on "
                                          "Facebook")
        self.assertEqual(gap[0]["fix"], "Do on YouTube what works on Facebook. Facebook against YouTube: length 30s "
                                        "against 40s, own cut on 8 of 8 against 0 of 8, titles 80 against 50 "
                                        "characters, posted about 06:00 against 06:00. The same clips go to both, so "
                                        "check YouTube itself for a copyright claim or a limited video in YouTube "
                                        "Studio.")
        md = L.markdown(mini_rep(probs))
        self.assertIn("1. YouTube is Gaming's weakest platform: 13 views is usual there, 253 on Facebook.\n", md)
        self.assertEqual(gap[0]["overlaps"], True)                                         # not one day's loss
        two = vrows([13] * 8) + vrows([20] * 8, "instagram_reels") + vrows([253] * 8, "facebook_reels")
        words = [p["words"] for p in L.problems(self.cfg, self.gta, two, [], D, self.now, [], (D, D))
                 if p["kind"] == "gap"]
        self.assertEqual(words, ["Instagram is far behind Facebook on Gaming: 20 views is usual there, 253 on "
                                 "Facebook", "YouTube is Gaming's weakest platform: 13 views is usual there, 253 "
                                 "on Facebook"])

    def test_what_is_not_a_gap(self):
        self.assertEqual(L.gaps(self.cfg, self.gta, vrows([13] * 7) + vrows([253] * 8, "facebook_reels"), D), [])
        self.assertEqual(L.gaps(self.cfg, self.gta, vrows([26] * 8) + vrows([253] * 8, "facebook_reels"), D), [])
        self.assertEqual(L.gaps(self.cfg, self.gta, vrows([13] * 8), D), [])               # one platform: no gap

    # 4. a Which list cut short ends with how many more, so the count and the list agree
    def test_which_ends_with_how_many_more(self):
        ev = [{"board": D.isoformat(), "slot": f"{h:02d}:00"} for h in range(5, 15)]
        self.assertEqual(L.which_words({"n": 7, "evidence": ev[:7]}),
                         "07.14 05:00, 07.14 06:00, 07.14 07:00, 07.14 08:00, 07.14 09:00, and 2 more")
        self.assertTrue(L.which_words({"n": 15, "evidence": ev}).endswith(", and 10 more"))   # evidence keeps 10
        self.assertEqual(L.which_words({"n": 2, "evidence": ev[:2]}), "07.14 05:00, 07.14 06:00")
        self.sixty()
        self.board(D, {f"{h:02d}:00": self.slot(D, f"{h:02d}:00", status="failed") for h in range(5, 12)})
        L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        md = L.report_path(self.gta, D, "md").read_text()
        self.assertIn("1. 7 slots failed to make: about", md)
        self.assertIn("   Which: 07.14 05:00, 07.14 06:00, 07.14 07:00, 07.14 08:00, 07.14 09:00, and 2 more.\n", md)

    # 5. the report's own words, read against the files
    def test_the_report_says_what_it_rests_on(self):
        rep = mini_rep([], estimate=True, estimate_n=3, n_24h=19)
        self.assertEqual(L.estimate_words(rep), "3 of the 19 posts with a 24-hour number have it from the stats "
                                                "snapshots, not a checkpoint (an estimate).")
        self.assertIn("read off the stats snapshots (an estimate) until checkpoint", L.estimate_words(
            dict(rep, estimate_n=19)))
        self.assertEqual(L.estimate_words(dict(rep, estimate=False, estimate_n=0)), "")
        md = L.markdown(rep)
        self.assertIn("None open.", md)
        self.assertNotIn("0 open", md)
        self.assertEqual(L.none_words(dict(rep, window={"from": "2031-07-13", "to": "2031-07-14"},
                                           window_n={"with_24h": 6, "with_usual": 0}), True),
                         "None of the 6 posts that went up 07.13 to 07.14 with a 24-hour number has a usual to "
                         "compare with yet.")
        self.sixty()
        rep = L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        self.assertEqual(rep["health"]["words"].split(";")[0],
                         "2 posts went out from 1 signed clip, 1 failed or gave up (67% went through)")


if __name__ == "__main__":
    unittest.main()
