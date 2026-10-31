import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The social layer's state (store.py), switches (switches.py) and verdicts (verdicts.py), 2026-10-19: every part is off
# until it is switched on, a live value waits for its go-live checklist, caps only lower, the warm-up and quiet-hour
# clocks, every write kept with a history row and a dated copy, Decline's reasons as one line each. Throwaway folders;
# no network.

import json
import shutil
import sys
import threading
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import store               # noqa: E402
from clipfactory import switches as SW      # noqa: E402
from clipfactory import verdicts as VD      # noqa: E402

NOW = datetime(2031, 7, 8, 16, 0, tzinfo=timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-switches-"))
        self.old_state, C.STATE = C.STATE, self.tmp / "state"
        self.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = self.tmp / "keys"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        raw["review_port"] = 0
        for a in raw["accounts"].values():
            a["delivered"] = [str(self.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["account_dir"] = str(self.tmp / "accounts" / a["name"])
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        REG.reset()
        self.addCleanup(REG.reset)

    def tearDown(self):
        C.STATE = self.old_state
        O.TOKENS, D.TOKENS = self.old_tokens
        shutil.rmtree(self.tmp / "keys", ignore_errors=True)

    def inject(self, name: str, mod) -> None:
        key = "clipfactory." + name
        if key in sys.modules:
            self.addCleanup(sys.modules.__setitem__, key, sys.modules[key])
        else:
            self.addCleanup(sys.modules.pop, key, None)
        sys.modules[key] = mod
        REG.reset()


class StoreTest(Base):
    def test_1_read_edit_write_append_rows(self):
        p = store.path("gaming", "social", "x.json")
        self.assertEqual(p, C.STATE / "gaming" / "social" / "x.json")
        self.assertEqual(store.read(p, {"d": 1}), {"d": 1})
        with store.edit(p, dict) as d:
            d["a"] = 1
        self.assertEqual(store.read(p), {"a": 1})
        self.assertEqual(p.read_text(), '{\n "a": 1\n}')                  # indent 1, as the boards
        with self.assertRaises(RuntimeError):
            with store.edit(p, dict) as d:
                d["a"] = 2
                raise RuntimeError("half way")
        self.assertEqual(store.read(p), {"a": 1})                          # nothing written
        p.write_text("{broken")
        self.assertEqual(store.read(p, "dflt"), "dflt")
        with store.edit(p, list) as rows:                                  # broken or another type: the default
            rows.append(1)
        self.assertEqual(store.read(p), [1])
        log = store.path("gaming", "learn", "v.jsonl")
        store.append(log, {"at": "2031-07-01T10:00:00+00:00", "n": 1})
        with open(log, "a") as f:
            f.write("{half a line\n\n")
        store.append(log, {"t": "2031-07-08T10:00:00+00:00", "n": 2})
        self.assertEqual([r["n"] for r in store.rows(log)], [1, 2])
        self.assertEqual([r["n"] for r in store.rows(log, since=date(2031, 7, 5))], [2])
        self.assertEqual(store.rows(store.path("nothing.jsonl")), [])

    def test_2_writes_refuse_outside_the_state(self):
        out = self.tmp / "elsewhere.json"
        for fn in (lambda: store.write(out, {}), lambda: store.append(out, {})):
            with self.assertRaises(SystemExit):
                fn()
        with self.assertRaises(SystemExit):
            with store.edit(C.STATE / ".." / "escape.json", dict):
                pass
        self.assertFalse(out.exists())

    def test_3_prune_months_keeps_recent(self):
        d = store.path("gaming", "outward")
        d.mkdir(parents=True)
        for name in ("actions-2031-03.jsonl", "actions-2031-05.jsonl", "actions-2031-07.jsonl", "keys.json",
                     "actions-junk.jsonl"):
            (d / name).write_text("")
        gone = store.prune_months(d, "actions-", 30, now=NOW)
        self.assertEqual(gone, ["actions-2031-03.jsonl", "actions-2031-05.jsonl"])
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["actions-2031-07.jsonl", "actions-junk.jsonl",
                                                               "keys.json"])

    def test_4_try_lock_skips_when_busy(self):
        seen = []
        with store.try_lock("outward:drain_job") as a:
            seen.append(a)
            ready = threading.Event()

            def other():
                with store.try_lock("outward:drain_job") as b:
                    seen.append(b)
                ready.set()
            threading.Thread(target=other).start()
            ready.wait(5)
        with store.try_lock("outward:drain_job") as c:
            seen.append(c)
        self.assertEqual(seen, [True, False, True])
        self.assertTrue((C.STATE / "social" / "locks" / "outward-drain_job.lock").exists())


class SwitchesTest(Base):
    def test_1_everything_starts_off(self):
        for k in SW.MODES:
            self.assertEqual(SW.mode(self.gta, k), "off", k)
        self.assertFalse(SW.any_on(self.gta))
        s = SW.settings(self.gta)
        self.assertEqual(s["warmup_days"], 28)
        self.assertEqual(s["quiet_hours"], ["00:30", "07:00"])
        self.assertEqual(s["cards.kinds"], ["countdown", "clip_of_day", "week_best"])
        self.assertEqual(SW.settings(self.politics)["cards.kinds"], ["quote", "docket", "week_best", "news"])
        self.assertEqual(SW.global_settings(), {"v": 1, "youtube_units": 2500})
        self.assertEqual(SW.doctor(self.cfg)[0], (None, "Gaming: every social part is off", ""))
        self.assertFalse((C.STATE / "gaming").exists())                 # reading writes nothing

    def test_2_set_mode_validates_and_keeps_history_and_a_copy(self):
        self.assertEqual(SW.set_mode(self.cfg, self.gta, "guard", "watch", "Andrew", now=NOW),
                         "Gaming: guard is now watch (was off)")
        self.assertEqual(SW.mode(self.gta, "guard"), "watch")
        self.assertEqual(SW.mode("gaming", "guard"), "watch")              # a slug reads the same file
        self.assertEqual(SW.mode(self.politics, "guard"), "off")
        for key, value in (("guard", "practice"), ("nothing", "on"), ("coach", "testers")):
            with self.assertRaises(SystemExit):
                SW.set_mode(self.cfg, self.gta, key, value, "Andrew")
        SW.set_mode(self.cfg, "gaming", "first_comment.facebook_reels", "practice", "Andrew", page=True, now=NOW)
        raw = json.loads((C.STATE / "gaming" / "social" / "settings.json").read_text())
        self.assertEqual(raw["modes"], {"guard": "watch", "first_comment.facebook_reels": "practice"})
        self.assertEqual([(h["key"], h["from"], h["to"], h["via"]) for h in raw["history"]],
                         [("guard", "off", "watch", "command"),
                          ("first_comment.facebook_reels", "off", "practice", "page")])
        self.assertEqual(len(list((C.STATE / "config-backups").glob("social-gaming-*.json"))), 2)
        self.assertEqual(SW.doctor(self.cfg)[0][1],
                         "Gaming: on: guard watch, first_comment.facebook_reels practice")
        self.assertEqual(SW.set_mode(self.cfg, self.gta, "guard", "watch", "Andrew"), "Gaming: guard is already watch")

    def test_3_a_bad_file_or_value_reads_off_and_doctor_says_no(self):
        p = C.STATE / "gaming" / "social" / "settings.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"modes": {"guard": "maybe", "coach": "on", "made_up": "on"},
                                 "settings": {"warmup_days": 3, "engage.poll_minutes": 10, "colour": "red"}}))
        self.assertEqual(SW.mode(self.gta, "guard"), "off")
        self.assertEqual(SW.mode(self.gta, "coach"), "on")
        self.assertEqual(SW.settings(self.gta)["warmup_days"], 28)            # below the floor: the default
        probs = SW.problems(self.gta)
        self.assertEqual(len(probs), 4, probs)
        rows = SW.doctor(self.cfg)
        self.assertTrue(any(g is False and "warmup_days: 14 at the least" in t for g, t, _ in rows), rows)
        p.write_text("{not json")
        self.assertEqual(SW.mode(self.gta, "coach"), "off")
        self.assertIn("not readable JSON", SW.problems(self.gta)[0])

    def test_4_settings_types_floors_and_caps_only_lower(self):
        SW.set_setting(self.cfg, self.gta, "warmup_days", 21, "Andrew")
        self.assertEqual(SW.settings(self.gta)["warmup_days"], 21)
        for key, value in (("warmup_days", 7), ("engage.poll_minutes", 2), ("engage.keep_days", 60),
                           ("quiet_hours", ["1:00", "07:00"]), ("created", "yesterday"), ("messages_access", "full"),
                           ("caps", {"reply_public/auto": {"per_hour": 50}}), ("caps", {"posting": {"per_day": 1}}),
                           ("first_comment.delay_s", [300, 90]), ("ideas.max_channels", 40), ("colour", "red")):
            with self.assertRaises(SystemExit, msg=f"{key}={value}"):
                SW.set_setting(self.cfg, self.gta, key, value, "Andrew")
        self.assertEqual(SW.caps_for(self.gta, "reply_public", False), (6, 30, 60, 180))
        SW.set_setting(self.cfg, self.gta, "caps", {"reply_public/auto": {"per_hour": 3, "gap_s": 20},
                                                    "dm": {"per_day": 10, "jitter_s": 600}}, "Andrew")
        self.assertEqual(SW.caps_for(self.gta, "reply_public", False), (3, 30, 60, 180))   # a gap only lengthens
        self.assertEqual(SW.caps_for(self.gta, "reply_public", True), (20, 120, 30, 60))
        self.assertEqual(SW.caps_for(self.gta, "dm", True), (20, 10, 20, 600))
        self.assertEqual(SW.caps_for(self.gta, "hide", False), (0, 0, 0, 0))               # never automated
        self.assertEqual(SW.HARD[("story", "auto")], (2, 6, 2700, 900))
        self.assertEqual(SW.FAMILY_HARD["engagement"], (40, 250))
        self.assertEqual((SW.IG_PUBLISH_DAY, SW.REEL_GAP_S), (25, 1800))

    def test_5_testers(self):
        SW.add_tester(self.cfg, self.gta, "instagram", "1784", "Andrew's own", "Andrew")
        SW.add_tester(self.cfg, self.gta, "instagram", "1784", "Andrew (again)", "Andrew")
        self.assertEqual(SW.settings(self.gta)["testers"], [{"platform": "instagram", "id": "1784",
                                                             "name": "Andrew (again)"}])
        self.assertTrue(SW.is_tester(self.gta, "instagram", "1784"))
        self.assertTrue(SW.is_tester(self.gta, "instagram:1784"))
        self.assertFalse(SW.is_tester(self.gta, "facebook", "1784"))
        with self.assertRaises(SystemExit):
            SW.add_tester(self.cfg, self.gta, "tiktok", "1", "x", "Andrew")
        self.assertIn("no longer", SW.remove_tester(self.cfg, self.gta, "instagram", "1784", "Andrew"))
        self.assertFalse(SW.is_tester(self.gta, "instagram:1784"))

    def test_6_warm_up_needs_created_and_a_first_post(self):
        self.assertIsNone(SW.warmup_until(self.cfg, self.gta))
        SW.set_setting(self.cfg, self.gta, "created", "2031-06-01", "Andrew")
        self.assertIsNone(SW.warmup_until(self.cfg, self.gta))             # nothing posted yet: still warming up
        board = C.STATE / "gaming" / "2031-06-10.json"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_text(json.dumps({"slots": {"07:00": {"outlets": {"facebook_reels": {
            "post_id": "1", "published_at": "2031-06-10T13:00:00+00:00"}}}}}))
        self.assertEqual(SW.warmup_until(self.cfg, self.gta), date(2031, 6, 29))    # created + 28
        SW.set_setting(self.cfg, self.gta, "warmup_days", 14, "Andrew")
        self.assertEqual(SW.warmup_until(self.cfg, self.gta), date(2031, 6, 24))    # first post + 14

    def test_7_quiet_hours(self):
        tz = self.cfg.tz()
        at = lambda h, m: datetime(2031, 7, 8, h, m, tzinfo=tz)       # noqa: E731
        self.assertEqual(SW.quiet(self.cfg, self.gta, at(3, 0)), at(7, 0))
        self.assertIsNone(SW.quiet(self.cfg, self.gta, at(7, 0)))
        self.assertIsNone(SW.quiet(self.cfg, self.gta, at(0, 29)))
        SW.set_setting(self.cfg, self.gta, "quiet_hours", ["23:00", "06:00"], "Andrew")
        self.assertEqual(SW.quiet(self.cfg, self.gta, at(23, 30)), at(6, 0) + timedelta(days=1))
        self.assertEqual(SW.quiet(self.cfg, self.gta, at(5, 0)), at(6, 0))
        self.assertIsNone(SW.quiet(self.cfg, self.gta, at(12, 0)))

    def test_8_going_live_needs_the_checklist(self):
        key = "first_comment.facebook_reels"
        with self.assertRaises(SystemExit) as ex:
            SW.set_mode(self.cfg, self.gta, key, "on", "Andrew", now=NOW)
        self.assertIn("stays off; before on: practice for 2 days", str(ex.exception))
        self.assertIn("missing (not read yet: factory.py social probe gaming scopes)", str(ex.exception))
        start = NOW - timedelta(days=3)
        SW.set_mode(self.cfg, self.gta, key, "practice", "Andrew", now=start)
        rows = [{"t": (start + timedelta(hours=6 * i)).isoformat(), "mode": "practice", "kind": "first_comment",
                 "outlet": "facebook_reels", "state": "practice"} for i in range(10)]
        rows.append({"t": NOW.isoformat(), "mode": "practice", "kind": "first_comment", "outlet": "instagram_reels",
                     "state": "held"})                                  # another outlet's: not counted
        ow = types.ModuleType("clipfactory.outward")
        ow.rows = lambda cfg, slug, since=None, **kw: [r for r in rows if r["t"] >= since.isoformat()]
        cap = types.ModuleType("clipfactory.capabilities")
        missing = {"facebook": ["pages_manage_engagement"]}
        cap.missing = lambda acct, platform, need: missing.get(platform, [])
        self.inject("outward", ow)
        self.inject("capabilities", cap)
        items = SW.checklist(self.cfg, self.gta, key, NOW)
        self.assertEqual([d for d, _ in items], [True, True, False, False])
        self.assertIn("3 days and 10 rows so far", items[0][1])
        self.assertIn("missing pages_manage_engagement", items[3][1])
        with self.assertRaises(SystemExit):
            SW.ack_practice(self.cfg, self.gta, key, "Andrew", page=False)
        SW.ack_practice(self.cfg, self.gta, key, "Andrew", page=True, now=NOW)
        missing.clear()
        self.assertEqual([d for d, _ in SW.checklist(self.cfg, self.gta, key, NOW)], [True, True, True, True])
        rows.append({"t": (NOW + timedelta(seconds=30)).isoformat(), "mode": "practice", "kind": "first_comment",
                     "outlet": "facebook_reels", "state": "held"})     # over a hold, after he read them
        items = SW.checklist(self.cfg, self.gta, key, NOW + timedelta(minutes=1))
        self.assertEqual([d for d, _ in items], [True, False, False, True])
        rows.pop()
        self.assertEqual(SW.set_mode(self.cfg, self.gta, key, "on", "Andrew", now=NOW),
                         f"Gaming: {key} is now on (was practice)")
        auto = SW.checklist(self.cfg, self.gta, "automations", NOW)
        self.assertEqual(auto[-1][0], False)                              # no creation date yet
        self.assertEqual(SW.checklist(self.cfg, self.gta, "coach", NOW), [])
        SW.set_mode(self.cfg, self.gta, "coach", "on", "Andrew")          # no checklist: on at once

    def test_9_client_accounts_are_never_switched(self):
        client = C.Account(**{**self.gta.__dict__, "slug": "a-client", "name": "A Client",
                              "raw": {**self.gta.raw, "owner": "client"}})
        self.assertEqual(SW.mode(client, "coach"), "off")
        with self.assertRaises(SystemExit):
            SW.set_mode(self.cfg, client, "coach", "on", "Andrew")


    def _rows_and_caps(self, rows):
        ow = types.ModuleType("clipfactory.outward")
        ow.rows = lambda cfg, slug, since=None, **kw: [r for r in rows if r["t"] >= since.isoformat()]
        cap = types.ModuleType("clipfactory.capabilities")
        cap.missing = lambda acct, platform, need: []
        self.inject("outward", ow)
        self.inject("capabilities", cap)

    def test_10_the_checklist_counts_only_the_parts_own_would_sends(self):
        start = NOW - timedelta(days=3)
        SW.set_setting(self.cfg, self.gta, "created", "2031-01-01", "Andrew")
        SW.set_mode(self.cfg, self.gta, "automations", "practice", "Andrew", now=start)
        rows = [{"t": (start + timedelta(hours=3 * i)).isoformat(), "mode": "practice", "state": "practice",
                 "kind": "dm", "signed": {"what": "click"}, "origin": {"by": "Andrew", "via": "page"}}
                for i in range(20)]                                     # his own Inbox sends: never automations'
        rows += [{"t": (start + timedelta(hours=3 * i + 1)).isoformat(), "mode": "practice", "state": "skipped",
                  "code": "signed", "kind": "reply_public", "signed": {"what": "rule"},
                  "origin": {"by": "rule", "rule": "r1"}} for i in range(20)]   # skipped: not would-sends
        self._rows_and_caps(rows)
        self.assertEqual(SW._feature_rows(self.cfg, self.gta, "automations", start), [])
        items = SW.checklist(self.cfg, self.gta, "automations", NOW)
        self.assertFalse(items[0][0])
        self.assertIn("3 days and 0 rows so far", items[0][1])
        SW.ack_practice(self.cfg, self.gta, "automations", "Andrew", page=True, now=NOW)
        items = SW.checklist(self.cfg, self.gta, "automations", NOW)
        self.assertEqual([d for d, _ in items], [False, True, False, True, True])
        with self.assertRaises(SystemExit):
            SW.set_mode(self.cfg, self.gta, "automations", "on", "Andrew", now=NOW)
        rows += [{"t": (start + timedelta(hours=3 * i + 2)).isoformat(), "mode": "practice", "state": "practice",
                  "kind": "reply_public", "signed": {"what": "rule"}, "origin": {"by": "rule", "rule": "r1"}}
                 for i in range(20)]                                    # a rule's would-sends: these count
        self.assertEqual(len(SW._feature_rows(self.cfg, self.gta, "automations", start)), 20)
        SW.ack_practice(self.cfg, self.gta, "automations", "Andrew", page=True, now=NOW)
        self.assertEqual([d for d, _ in SW.checklist(self.cfg, self.gta, "automations", NOW)], [True] * 5)
        rows.append({"t": (NOW - timedelta(hours=1)).isoformat(), "mode": "practice", "state": "held", "code": "cap",
                     "kind": "dm", "origin": {"by": "rule", "rule": "r1"}})   # a rule's row over a cap: item 2
        self.assertEqual([d for d, _ in SW.checklist(self.cfg, self.gta, "automations", NOW)],
                         [True, False, True, True, True])

    def test_11_skipped_first_comments_never_fill_the_quota(self):
        key = "first_comment.facebook_reels"
        start = NOW - timedelta(days=3)
        SW.set_mode(self.cfg, self.gta, key, "practice", "Andrew", now=start)
        rows = [{"t": (start + timedelta(hours=6 * i)).isoformat(), "mode": "practice", "state": "skipped",
                 "code": "signed", "kind": "first_comment", "outlet": "facebook_reels"} for i in range(10)]
        self._rows_and_caps(rows)
        items = SW.checklist(self.cfg, self.gta, key, NOW)
        self.assertEqual((items[0][0], items[2][0]), (False, False))
        self.assertIn("3 days and 0 rows so far", items[0][1])
        for r in rows:
            r["state"] = "practice"
        self.assertIn("3 days and 10 rows so far", SW.checklist(self.cfg, self.gta, key, NOW)[0][1])

    def test_12_an_impossible_created_day_is_a_refusal_and_never_breaks_a_reader(self):
        for bad in ("2026-02-30", "2026-13-01"):
            with self.assertRaises(SystemExit) as ex:
                SW.set_setting(self.cfg, self.gta, "created", bad, "Andrew")
            self.assertIn("a real day", str(ex.exception))
        p = SW.file_path(self.gta)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"v": 1, "settings": {"created": "2026-02-30"}}))   # a hand edit
        self.assertEqual(SW.settings(self.gta)["created"], "")
        self.assertEqual(SW.problems(self.gta), ["created: YYYY-MM-DD, a real day (the default is used)"])
        self.assertIn(False, [ok for ok, _, _ in SW.doctor(self.cfg)])
        SW.quiet(self.cfg, self.gta, NOW)
        SW.caps_for(self.gta, "dm", False)
        self.assertIn("2026-02-01", SW.set_setting(self.cfg, self.gta, "created", "2026-02-01", "Andrew"))
        self.assertEqual(SW.settings(self.gta)["created"], "2026-02-01")
        self.assertEqual(SW.problems(self.gta), [])

    def test_13_card_times_are_checked(self):
        for bad in ({"countdown": "nope"}, {"countdown": 9}, {"clip_of_day": "25:00"}, {"docket": "9:00"},
                    {"week_best": "Funday 19:00"}, {"week_best": "Mon"}, {"week_best": 3},
                    {"quote_after_min": "x"}, {"quote_after_min": [20, 40]}, {"quote_after_min": [90, 60]},
                    {"quote_after_min": [60.5, 90]}, {"quote_after_min": [True, 90]}, {"bogus": 1}, ["09:00"]):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                SW.set_setting(self.cfg, self.gta, "cards.times", bad, "Andrew")
        good = {"countdown": "08:15", "week_best": "Fri 18:30", "quote_after_min": [45, 75]}
        SW.set_setting(self.cfg, self.gta, "cards.times", good, "Andrew")
        self.assertEqual(SW.settings(self.gta)["cards.times"], good)
        SW.set_setting(self.cfg, self.gta, "cards.times", {"week_best": "sunday 07:00"}, "Andrew")
        p = SW.file_path(self.gta)
        raw = json.loads(p.read_text())
        raw["settings"]["cards.times"] = {"countdown": "nope"}            # a hand edit
        p.write_text(json.dumps(raw))
        self.assertEqual(SW.settings(self.gta)["cards.times"], SW.SETTINGS["cards.times"])
        self.assertEqual(len(SW.problems(self.gta)), 1)
        self.assertIn("countdown", SW.problems(self.gta)[0])
        self.assertIn(False, [ok for ok, _, _ in SW.doctor(self.cfg)])


class VerdictsTest(Base):
    def test_1_chips_check_words(self):
        self.assertEqual(len(VD.CHIPS), 11)                      # the Reason codes list (Andrew's pick, 10-20)
        self.assertEqual(VD.CHIPS[0], ["wrong-name", "Wrong name"])
        self.assertIn(["repeat", "Repeat"], VD.CHIPS)
        self.assertEqual([c[1] for c in VD.CHIPS][-3:], ["Hook", "Rights", "Other"])
        self.assertEqual(VD.check(["hook", "framing", "weak_hook"]), ["hook", "framing"])   # old code: hook again
        self.assertEqual(VD.check("hook, framing"), ["hook", "framing"])
        self.assertEqual(VD.check(None), [])
        for bad in (["weak"], [1], {"a": 1}):
            with self.assertRaises(SystemExit):
                VD.check(bad)
        self.assertEqual(VD.words(["hook", "repeat"]), "hook, repeat")

    def test_2_record_and_rows(self):
        slot = {"slot": "08:45", "slug": "the-thing", "clip": {"sha256": "abc"}, "format": "pip",
                "host": "MrBossFTW", "job": "factory-20310708-pip", "source_url": "https://www.youtube.com/watch?v=x"}
        row = VD.record(self.cfg, self.gta, date(2031, 7, 8), slot, "decline", "Andrew", ["framing"], "too dark",
                        now=NOW)
        self.assertEqual(row, {"at": "2031-07-08T16:00:00+00:00", "by": "Andrew", "verdict": "decline",
                               "board": "2031-07-08", "slot": "08:45", "slug": "the-thing", "clip_sha": "abc",
                               "format": "pip", "host": "MrBossFTW", "job": "factory-20310708-pip",
                               "source_url": "https://www.youtube.com/watch?v=x", "reasons": ["framing"],
                               "note": "too dark"})
        VD.record(self.cfg, self.gta, "2031-07-09", slot, "approve", "Andrew", now=NOW + timedelta(days=1))
        self.assertEqual([r["verdict"] for r in VD.rows(self.gta)], ["decline", "approve"])
        self.assertEqual([r["verdict"] for r in VD.rows("gaming", since=NOW + timedelta(hours=1))], ["approve"])
        self.assertTrue((C.STATE / "gaming" / "learn" / "verdicts.jsonl").is_file())
        with self.assertRaises(SystemExit):
            VD.record(self.cfg, self.gta, "2031-07-09", slot, "maybe", "Andrew")
        with self.assertRaises(SystemExit):
            VD.record(self.cfg, self.gta, "2031-07-09", slot, "decline", "Andrew", ["nope"])


if __name__ == "__main__":
    unittest.main()
