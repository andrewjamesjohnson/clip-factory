import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The scout reads the playbook (2026-10-20): scout.run adds the approved factors after load() (so dated phases
# win), score() multiplies by learned_factor (exactly 1.0 when there is none, and on any error), rank() shows the
# factor on a find only when it moved it. An absent playbook, or the switch off, changes nothing at all (the
# byte-for-byte pin is tests/test_scout_same_output.py). Throwaway folders; nothing fetched.

import copy
import json
import math
import shutil
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C         # noqa: E402
from clipfactory import learn as L          # noqa: E402
from clipfactory import playbook as PB      # noqa: E402
from clipfactory import scout as SC         # noqa: E402
from clipfactory import store               # noqa: E402

RULES = json.loads((ROOT / "tests" / "fixtures" / "scout_same_rules.json").read_text())
NOW = datetime(2031, 3, 4, 18, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="seconds")


def vid(i, title, channel, vph, age=2.0, views=None, duration=600):
    return {"id": i, "kind": "video", "url": f"https://www.youtube.com/watch?v={i}", "title": title,
            "channel": channel, "channel_slug": None, "source": channel, "given_rights": None, "weight": 1.0,
            "published": "2031-03-04T16:00:00+00:00", "age_h": age, "views": views or int(vph * age), "vph": vph,
            "duration": duration, "was_live": False, "short": False}


def politics_raw():
    return [
        vid("warren00001", "Warren yells at bank CEO who will not answer", "C-SPAN", 20000),
        vid("paris000001", "Trump clashes with reporters in Paris", "CBS News", 15000, age=3),
        vid("lens0000001", "Senate hearing erupts", "Washington Lens", 30000),
        vid("hegs0000001", "Hegseth grilled over boat strikes", "NBC News", 150000, age=3),
    ]


def many_raw():
    """Twelve close hearing videos, one channel each: a 1.6x factor moves the last into the top 10."""
    return [vid(f"hear{i:07d}", f"Senate hearing number {i} on the bank failures", f"Channel {i}", 10000 - i * 120)
            for i in range(12)]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-scout-playbook-"))
        self.old = C.STATE
        C.STATE = self.tmp / "st"           # not "state": 3.13 rmtree opens subfolders by bare name
        self.addCleanup(setattr, C, "STATE", self.old)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        common = dict(delivered=[], slots=["07:00"], daily_target=1, clips_per_source=3, platforms=[], outlets={})
        self.led = C.Account(slug="politics", name="Politics", handle="", maker="politics",
                             skill_dir=self.tmp, account_dir=self.tmp / "politics", min_seconds=65, max_seconds=90,
                             formats=["room"], **common)
        self.cfg = C.Config(timezone="America/Denver", drafts_root=self.tmp / "drafts", review_port=0,
                            approvers=["Andrew"], accounts={"politics": self.led}, path=self.tmp / "a.json")

    def switch(self, acct, key, value):
        with store.edit(store.path(acct.slug, "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})[key] = value

    def rules(self):
        return SC.phased(copy.deepcopy(RULES["politics"]), "2031-03-04")

    def rank(self, scfg, raw=None):
        finds, _, _ = SC.rank(copy.deepcopy(raw or politics_raw()), scfg, self.led, float(scfg.get("hours", 36)), {},
                              {}, {}, STAMP, now=NOW)
        return {f["id"]: f for f in finds}


class AbsentChangesNothing(Base):
    def test_no_playbook_and_the_switch_on(self):
        self.switch(self.led, "learn.playbook", "on")
        scfg = self.rules()
        got = PB.apply_to_scout(self.led, scfg)
        self.assertIs(got, scfg)
        self.assertNotIn("learned", got)
        self.assertEqual(json.dumps(self.rank(got), sort_keys=True), json.dumps(self.rank(self.rules()),
                                                                                sort_keys=True))

    def test_a_playbook_with_the_switch_off(self):
        PB.apply(self.cfg, self.led, {"key": "scout.channels.c-span", "to": 1.5}, "Andrew", NOW)
        scfg = self.rules()
        self.assertIs(PB.apply_to_scout(self.led, scfg), scfg)
        for f in self.rank(scfg).values():
            self.assertNotIn("learned", f)

    def test_learned_factor_is_exactly_one_without_learned(self):
        self.assertEqual(PB.learned_factor({"channel": "C-SPAN"}, {}), 1.0)
        self.assertEqual(PB.learned_factor({"channel": "C-SPAN"}, {"learned": {}}), 1.0)
        self.assertEqual(PB.learned_factor({"channel": "CBS News"}, {"learned": {"channels": {"c-span": 1.5}}}), 1.0)


class FactorAppliesAfterPhases(Base):
    def test_run_reads_the_playbook_after_phases(self):
        raw_rules = copy.deepcopy(RULES["politics"])
        raw_rules["phases"] = [{"from": "2031-01-01", "set": {"half_life_h": 99}}]
        phased = SC.phased(raw_rules, "2031-03-04")
        self.assertEqual(phased["half_life_h"], 99)
        self.switch(self.led, "learn.playbook", "on")
        said = PB.apply(self.cfg, self.led, {"key": "scout.channels.C-SPAN", "to": 1.5, "id": "p-1"}, "Andrew", NOW)
        self.assertIn("version 1", said)
        seen = {}

        def gather(slug, scfg, acct, notes=None):
            seen["scfg"] = scfg
            return copy.deepcopy(politics_raw()), [], 4
        with mock.patch.object(SC, "load", lambda slug, today=None: copy.deepcopy(phased)), \
                mock.patch.object(SC, "gather", gather):
            SC.run(self.cfg, self.led)
        self.assertEqual(seen["scfg"]["half_life_h"], 99)                    # the phase still wins
        self.assertEqual(seen["scfg"]["learned"]["channels"], {"c-span": 1.5})
        day = self.cfg.now().date()
        board = json.loads((C.STATE / "politics" / "scout" / f"{day}.json").read_text())
        warren = board["finds"]["warren00001"]
        self.assertEqual(warren["learned"], 1.5)
        for k, f in board["finds"].items():
            if k != "warren00001":
                self.assertNotIn("learned", f)

    def test_score_is_multiplied(self):
        scfg = self.rules()
        plain = self.rank(scfg)
        scfg2 = dict(scfg, learned={"channels": {"c-span": 1.5}, "people": {}, "heat_words": {}})
        moved = self.rank(scfg2)
        self.assertAlmostEqual(moved["warren00001"]["score"], round(plain["warren00001"]["score"] * 1.5, 3),
                               delta=0.002)
        self.assertEqual(moved["paris000001"]["score"], plain["paris000001"]["score"])
        self.assertEqual(moved["warren00001"]["learned"], 1.5)

    def test_factors_multiply_and_stay_in_bounds(self):
        f = {"channel": "C-SPAN", "people": ["warren"], "heat_words": ["yells"]}
        L1 = {"learned": {"channels": {"c-span": 1.5}, "people": {"warren": 1.5}, "heat_words": {"yells": 1.5}}}
        self.assertEqual(PB.learned_factor(f, L1), 1.6)
        L2 = {"learned": {"channels": {"c-span": 0.5}, "people": {"warren": 0.5}, "heat_words": {}}}
        self.assertEqual(PB.learned_factor(f, L2), 0.5)
        L3 = {"learned": {"channels": {"c-span": 1.2}, "people": {"warren": 1.1}, "heat_words": {}}}
        self.assertAlmostEqual(PB.learned_factor(f, L3), 1.32)


class ExceptionGivesOne(Base):
    def test_bad_values(self):
        self.assertEqual(PB.learned_factor({"channel": "C-SPAN"}, {"learned": {"channels": {"c-span": "zz"}}}), 1.0)
        self.assertEqual(PB.learned_factor(None, {"learned": {"channels": {"c-span": 1.5}}}), 1.0)
        self.assertEqual(PB.learned_factor({"channel": "C-SPAN"}, {"learned": "nonsense"}), 1.0)
        self.assertEqual(PB.learned_factor({"channel": "C-SPAN"}, None), 1.0)

    def test_score_survives_a_raising_playbook(self):
        scfg = self.rules()
        plain = self.rank(scfg)
        scfg2 = dict(scfg, learned={"channels": {"c-span": 1.5}})
        with mock.patch.object(PB, "learned_factor", side_effect=RuntimeError("boom")):
            got = self.rank(scfg2)
        self.assertEqual({k: f["score"] for k, f in got.items()}, {k: f["score"] for k, f in plain.items()})
        self.assertNotIn("learned", got["warren00001"])

    def test_broken_playbook_file(self):
        self.switch(self.led, "learn.playbook", "on")
        p = PB.path(self.led)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json")
        scfg = self.rules()
        self.assertIs(PB.apply_to_scout(self.led, scfg), scfg)
        with mock.patch.object(PB, "scout_block", side_effect=OSError("disk")):
            self.assertIs(PB.apply_to_scout(self.led, scfg), scfg)


class FamilyAndBacktest(Base):
    def test_a_new_family_account_starts_from_the_family(self):
        common = dict(delivered=[], slots=["07:00"], daily_target=1, clips_per_source=3, platforms=[], outlets={})
        old = C.Account(slug="gaming", name="Gaming", handle="", maker="gta", skill_dir=self.tmp,
                        account_dir=self.tmp / "g", min_seconds=30, max_seconds=60, formats=["underlay"], **common)
        new = C.Account(slug="stunts", name="Stunts", handle="", maker="gta", skill_dir=self.tmp,
                        account_dir=self.tmp / "c", min_seconds=30, max_seconds=60, formats=["gameplay"], **common)
        PB.apply(self.cfg, old, {"key": "scout.channels.kai cenat", "to": 1.44}, "Andrew", NOW)
        self.assertEqual(PB.scout_block(new)["channels"], {"kai cenat": 1.44})       # 0 posts: the family's whole
        rows = [{"outlet": "youtube_shorts", "views": {"24h": 10}} for _ in range(12)]
        store.write(L.features_path(new), {"rows": rows})
        self.assertAlmostEqual(PB.scout_block(new)["channels"]["kai cenat"], math.exp(math.log(1.44) / 2), places=3)
        PB.apply(self.cfg, new, {"key": "scout.channels.speirs", "to": 1.2}, "Andrew", NOW)
        self.assertEqual(PB.scout_block(new)["channels"], {"speirs": 1.2})            # its own rules win
        self.assertEqual(PB.scout_block(self.led), {})                               # another family: nothing

    def test_backtest_reranks_saved_boards(self):
        scfg = self.rules()
        finds = self.rank(scfg, many_raw())
        top = sorted(finds.values(), key=lambda f: -f["score"])
        last = top[-1]
        self.assertGreaterEqual(len(top), 11)
        d = C.STATE / "politics" / "scout"
        d.mkdir(parents=True, exist_ok=True)
        (d / "2031-03-04.json").write_text(json.dumps({"updated": STAMP, "hours": 36, "finds": finds}))
        prop = {"kind": "factor", "key": f"scout.channels.{PB.name_key(last['channel'])}", "to": 1.6}
        bt = L.backtest(self.cfg, self.led, prop, scfg=scfg, now=NOW)
        self.assertEqual(bt["boards"], 1)
        self.assertEqual(bt["into_top10"], 1)
        self.assertEqual(bt["out_of_top10"], 1)
        self.assertIn(last["title"][:60], bt["examples"])
        self.assertEqual(L.backtest(self.cfg, self.led, {"key": "coach.first_word_max_s", "to": 1})["boards"], 0)


if __name__ == "__main__":
    unittest.main()
