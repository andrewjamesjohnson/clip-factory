import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# DM automations (rules.py), 2026-10-20: rules that answer what people ask, never flagged. The signature voids on an
# edit and never on a mode change; whole-word matching, plurals, phrases across lines, exclusions, intents; one run per
# comment, one message per person a day, one per person per rule; variants that rotate; the same words never twice in a
# day; quiet hours; testers mode and the warm-up practise; "Automated reply: " once per person; STOP; Part 2 within 6
# days and never twice; the rule that pauses itself; YouTube refused at sign; engagement bait and opinions refused at
# save; the starters; Try it and the 7-day replay are pure. The executor is faked; nothing reaches the network.

import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipfactory import capabilities as CAP    # noqa: E402
from clipfactory import outward as OW          # noqa: E402
from clipfactory import registry as REG        # noqa: E402
from clipfactory import rules as RU            # noqa: E402
from clipfactory import socialstore as SS      # noqa: E402
from clipfactory import store                  # noqa: E402
from clipfactory import switches as SW         # noqa: E402
from test_answers import DAY, GTA_URL, NOW, World, no_network   # noqa: E402

META_ALL = ["instagram_basic", "instagram_manage_comments", "pages_read_engagement", "pages_read_user_content",
            "pages_manage_engagement", "instagram_manage_messages", "pages_messaging", "pages_manage_metadata",
            "instagram_content_publish", "pages_manage_posts"]


def base_rule(**over) -> dict:
    r = {"name": "Where is it from", "platforms": ["instagram", "facebook"],
         "trigger": {"on": "comment", "posts": "any", "post_ids": [], "intents": ["asks_source"],
                     "keywords": ["source"], "exclude": [], "max_age_h": 24},
         "action": {"public": {"on": True, "variants": ["{mention} this is {host}, from {source_title}.",
                                                        "{mention} that clip comes from {host}.",
                                                        "{mention} it is out of {source_title} by {host}."]},
                    "private": {"on": False, "variants": []}, "part2": {"on": False}},
         "limits": {"per_hour": 6, "per_day": 30, "person_days": 7}}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k] = {**r[k], **v}
        else:
            r[k] = v
    return r


class FakeEngage(types.ModuleType):
    """The Inbox's executor, faked: what it was asked to send."""

    def __init__(self):
        super().__init__("clipfactory.engage")
        self.calls = []

    def execute(self, cfg, acct, action):
        self.calls.append(dict(action))
        return {"id": f"R{len(self.calls)}", "url": f"https://x/r{len(self.calls)}"}

    def find(self, cfg, acct, action):
        return None

    def still_signed(self, cfg, acct, action):
        return RU.still_signed(cfg, acct, action)

    def on_state(self, cfg, acct, action, state, result):
        return None


class Base(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.cfg, self.gta, self.politics = self.w.cfg, self.w.gta, self.w.politics
        no_network(self)
        RU.RNG.seed(7)
        OW.RNG.seed(7)

    # ------------------------------------------------------------------------------------------------ helpers
    def set(self, acct, modes=None, settings=None):
        p = SW.file_path(acct)
        raw = store.read(p, {}) or {}
        raw.setdefault("modes", {}).update(modes or {})
        raw.setdefault("settings", {}).update(settings or {})
        store.write(p, raw)
        SW._SEEN.clear()

    def grant(self, acct):
        CAP.save_scopes(acct, "meta", META_ALL, "connect")

    def warm(self, acct):
        self.set(acct, {}, {"created": "2031-01-01"})
        store.write(store.path(acct.slug, "2031-02-01.json"), {"slots": {"07:00": {"outlets": {
            "facebook_reels": {"post_id": "1", "published_at": "2031-02-01T14:00:00+00:00"}}}}})

    def add(self, acct, *items, now=NOW):
        SS.upsert_items(acct, list(items), now)
        return list(items)

    def save(self, acct, rule=None, now=NOW - timedelta(days=3)) -> str:
        rid, _ = RU.save(self.cfg, acct, rule or base_rule(), "Andrew", page=True, now=now)
        return rid

    def signed_live(self, acct, rule=None, mode="live", account_mode="on") -> str:
        rid = self.save(acct, rule)
        r = RU.get(acct, rid)
        RU.sign(self.cfg, acct, rid, RU.rule_hash(r), "Andrew", page=True, now=NOW - timedelta(days=2))
        self.set(acct, {"automations": account_mode})
        RU.set_mode(self.cfg, acct, rid, mode, "Andrew", page=True, now=NOW - timedelta(days=1))
        return rid

    def practice(self, acct):
        self.set(acct, {"automations": "practice"})

    def inject_engage(self) -> "FakeEngage":
        """Swap in the fake executor and put back whatever was there before: popping the entry would leave the
        real module out of sys.modules, so the next REG.load imports a second copy while engagepage keeps the
        first (the same save-and-restore as inject() in test_social_cli)."""
        key = "clipfactory.engage"
        if key in sys.modules:
            self.addCleanup(sys.modules.__setitem__, key, sys.modules[key])
        else:
            self.addCleanup(sys.modules.pop, key, None)
        self.addCleanup(REG.reset)
        eng = FakeEngage()
        sys.modules[key] = eng
        REG.reset()
        return eng

    def runs(self, acct):
        return SS.runs(acct)

    def queue(self, acct):
        return sorted(OW._items(acct.slug).values(), key=lambda i: i["at"])

    def gitem(self, text, who="111", name="sam", tags=None, at=NOW - timedelta(minutes=5), post=None, **kw):
        return self.w.item(self.gta, post or self.w.gta_post(), text, who=who, name=name, tags=tags, at=at, **kw)


class Signing(Base):
    def test_hash_voids_on_edit_not_on_mode_change(self):
        rid = self.save(self.gta)
        r = RU.get(self.gta, rid)
        self.assertEqual((r["version"], r["mode"], RU.signed_state(r)), (1, "practice", "unsigned"))
        RU.sign(self.cfg, self.gta, rid, RU.rule_hash(r), "Andrew", page=True, now=NOW)
        self.assertEqual(RU.signed_state(RU.get(self.gta, rid)), "signed")
        RU.set_mode(self.cfg, self.gta, rid, "paused", "Andrew", now=NOW)
        RU.set_mode(self.cfg, self.gta, rid, "practice", "Andrew", now=NOW)
        self.assertEqual(RU.signed_state(RU.get(self.gta, rid)), "signed")
        RU.save(self.cfg, self.gta, {**base_rule(name="A new name"), "id": rid}, "Andrew", page=True, now=NOW)
        r = RU.get(self.gta, rid)
        self.assertEqual((r["name"], r["version"], RU.signed_state(r)), ("A new name", 1, "signed"))
        changed = base_rule(limits={"per_hour": 3})
        RU.save(self.cfg, self.gta, {**changed, "id": rid}, "Andrew", page=True, now=NOW)
        r = RU.get(self.gta, rid)
        self.assertEqual((r["version"], r["signoff"], RU.signed_state(r)), (2, None, "changed"))
        self.assertTrue(any(f[0] is False and "changed after signing" in f[1] for f in RU.doctor(self.cfg, NOW)))
        RU.sign(self.cfg, self.gta, rid, RU.rule_hash(r), "Andrew", page=True, now=NOW)
        self.assertEqual(RU.signed_state(RU.get(self.gta, rid)), "signed")

    def test_sign_needs_the_hash_the_page_showed_and_the_page(self):
        rid = self.save(self.gta)
        with self.assertRaises(SystemExit):
            RU.sign(self.cfg, self.gta, rid, "stale", "Andrew", page=True)
        with self.assertRaises(SystemExit) as e:
            RU.sign(self.cfg, self.gta, rid, RU.rule_hash(RU.get(self.gta, rid)), "Andrew", page=False)
        self.assertIn("Only a click", str(e.exception))

    def test_youtube_refused_at_sign(self):
        rid = self.save(self.gta, base_rule(platforms=["instagram", "youtube"]))
        with self.assertRaises(SystemExit) as e:
            RU.sign(self.cfg, self.gta, rid, RU.rule_hash(RU.get(self.gta, rid)), "Andrew", page=True)
        self.assertIn("YouTube", str(e.exception))

    def test_overlap_refused_at_sign(self):
        a = self.save(self.gta)
        RU.sign(self.cfg, self.gta, a, RU.rule_hash(RU.get(self.gta, a)), "Andrew", page=True)
        b = self.save(self.gta, base_rule(name="Another", trigger={"keywords": ["source", "link please"]},
                                          action={"public": {"on": True, "variants": [
                                              "{mention} the video is by {host}.", "{mention} {host} made it.",
                                              "{mention} credit goes to {host} here."]}}))
        with self.assertRaises(SystemExit) as e:
            RU.sign(self.cfg, self.gta, b, RU.rule_hash(RU.get(self.gta, b)), "Andrew", page=True)
        self.assertIn("already answers", str(e.exception))

    def test_live_needs_signature_practice_and_the_account(self):
        rid = self.save(self.gta, now=NOW)
        with self.assertRaises(SystemExit) as e:
            RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=True, now=NOW)
        self.assertIn("sign it first", str(e.exception))
        RU.sign(self.cfg, self.gta, rid, RU.rule_hash(RU.get(self.gta, rid)), "Andrew", page=True, now=NOW)
        with self.assertRaises(SystemExit) as e:
            RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=True, now=NOW + timedelta(hours=2))
        self.assertIn("24 hours", str(e.exception))
        self.assertIn("switch is off", str(e.exception))
        self.set(self.gta, {"automations": "practice"})
        with self.assertRaises(SystemExit):
            RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=True, now=NOW + timedelta(days=2))
        self.set(self.gta, {"automations": "on"})
        with self.assertRaises(SystemExit) as e:
            RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=False, now=NOW + timedelta(days=2))
        self.assertIn("Only a click", str(e.exception))
        RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=True, now=NOW + timedelta(days=2))
        self.assertEqual(RU.get(self.gta, rid)["mode"], "live")

    def test_bait_refused_at_save(self):
        for words in ("{mention} comment SOURCE to get the link from {host}",
                      "{mention} tag a friend who loves {host}", "{mention} follow for more {host} clips",
                      "{mention} type LINK below for {host}'s video"):
            with self.subTest(words=words), self.assertRaises(SystemExit) as e:
                RU.save(self.cfg, self.gta, base_rule(action={"public": {"on": True, "variants": [
                    words, "{mention} this is {host}.", "{mention} from {source_title}."]}}), "Andrew", page=True)
            self.assertIn("engagement bait", str(e.exception))

    def test_politics_stays_neutral(self):
        with self.assertRaises(SystemExit) as e:
            RU.save(self.cfg, self.politics, base_rule(action={"public": {"on": True, "variants": [
                "{mention} {host} destroyed him here.", "{mention} this is from {host}.",
                "{mention} it aired on {date}."]}}), "Andrew", page=True)
        self.assertIn("neutral", str(e.exception))
        with self.assertRaises(SystemExit):
            RU.save(self.cfg, self.politics, base_rule(action={"public": {"on": True, "variants": [
                "{mention} wow, {host}!", "{mention} this is from {host}.", "{mention} it aired on {date}."]}}),
                "Andrew", page=True)

    def test_text_rules_at_save(self):
        cases = {
            "too alike": ["{mention} this is {host}, from {source_title}.", "{mention} this is {host} from {source_title}",
                          "{mention} a clip of {host}."],
            "no link in a public reply": ["{mention} watch at {source_url}", "{mention} this is {host}.",
                                          "{mention} out of {source_title}."],
            "names the person": ["this is {host}.", "{mention} that is {host}.", "{mention} out of {source_title}."],
            "one fact from the clip": ["{mention} hello there.", "{mention} that is {host}.",
                                     "{mention} out of {source_title}."],
            "long dashes": ["{mention} this is {host} \u2014 live.", "{mention} that is {host}.",
                            "{mention} out of {source_title}."],
            "3 to 6": ["{mention} that is {host}."],
        }
        for want, vs in cases.items():
            with self.subTest(want=want), self.assertRaises(SystemExit) as e:
                RU.save(self.cfg, self.gta, base_rule(action={"public": {"on": True, "variants": vs}}), "Andrew",
                        page=True)
            self.assertIn(want, str(e.exception))
        with self.assertRaises(SystemExit) as e:          # a link in a message only answers a source ask
            RU.save(self.cfg, self.gta, base_rule(trigger={"intents": ["asks_who"]}, action={
                "private": {"on": True, "variants": ["Full video: {source_url}"]}}), "Andrew", page=True)
        self.assertIn("a link only goes", str(e.exception))
        with self.assertRaises(SystemExit) as e:
            RU.save(self.cfg, self.gta, base_rule(limits={"per_hour": 20}), "Andrew", page=True)
        self.assertIn("hard cap", str(e.exception))

    def test_starters_pass_and_seed_once(self):
        for acct in (self.gta, self.politics):
            for r in RU.starters(acct):
                self.assertEqual(RU.problems(acct, r), [], r["name"])
            got = RU.seed(self.cfg, acct, "Andrew", now=NOW)
            self.assertIn("3 starter rules", got)
            rl = RU.rules(acct)
            self.assertTrue(all(r["mode"] == "practice" and r["starter"] and r["signoff"] is None for r in rl))
            with self.assertRaises(SystemExit):
                RU.seed(self.cfg, acct, "Andrew", now=NOW)
        self.assertEqual([r["name"] for r in RU.rules(self.politics)],
                         ["Where is this from", "Who is that", "What hearing is this"])
        self.assertEqual([r["name"] for r in RU.rules(self.gta)],
                         ["Whose stream is this", "When does GTA 6 come out", "Part 2 please"])

    def test_delete_only_drafts_and_duplicate_as_draft(self):
        rid = self.signed_live(self.gta)
        with self.assertRaises(SystemExit):
            RU.delete(self.cfg, self.gta, rid, "Andrew", page=True)
        got = RU.duplicate(self.cfg, self.gta, rid, self.politics, "Andrew", page=True, now=NOW)
        self.assertIn("as a draft", got)
        copy = RU.rules(self.politics)[0]
        self.assertEqual((copy["mode"], copy["signoff"], copy["version"]), ("practice", None, 1))
        self.assertIn(rid, copy["copied_from"])
        d = self.save(self.gta, base_rule(name="Scratch", trigger={"keywords": ["scratch"], "intents": []}))
        RU.delete(self.cfg, self.gta, d, "Andrew", page=True)
        self.assertIsNone(RU.get(self.gta, d))


class Matching(Base):
    def test_whole_words_plurals_phrases(self):
        self.assertEqual(RU.keyword_hits("Sources please", ["source"]), ["source"])
        self.assertEqual(RU.keyword_hits("a resource", ["source"]), [])
        self.assertEqual(RU.keyword_hits("the FULL\n  video pls", ["full video"]), ["full video"])
        self.assertEqual(RU.keyword_hits("fullvideo", ["full video"]), [])

    def test_exclude_vetoes_and_intents_match(self):
        r = {**base_rule(trigger={"exclude": ["fake"], "keywords": ["source"], "intents": ["asks_who"]}), "id": "x"}
        self.assertIsNone(RU.match(r, {"text": "source? this is fake", "at": NOW.isoformat()}, [], NOW))
        self.assertEqual(RU.match(r, {"text": "who is he", "at": NOW.isoformat()}, ["asks_who"], NOW),
                         {"intents": ["asks_who"], "keywords": []})
        self.assertIsNone(RU.match(r, {"text": "who is he", "at": (NOW - timedelta(hours=30)).isoformat()},
                                   ["asks_who"], NOW))
        listed = {**base_rule(trigger={"posts": "list", "post_ids": ["IG9"]}), "id": "y"}
        self.assertIsNone(RU.match(listed, {"text": "source", "post": {"platform_id": "IG1"}}, [], NOW))

    def test_fallback_tags(self):
        self.assertIn("asks_source", RU.tags_for("where is this from?"))
        self.assertIn("asks_part2", RU.tags_for("PART 2 PLEASE"))
        self.assertIn("stop", RU.tags_for("stop messaging me"))


class Runs(Base):
    def setUp(self):
        super().setUp()
        self.practice(self.gta)
        self.rid = self.save(self.gta)

    def test_off_means_off(self):
        self.set(self.gta, {"automations": "off"})
        self.add(self.gta, self.gitem("source?"))
        self.assertEqual(RU.on_items(self.cfg, self.gta, SS.items(self.gta), NOW), [])
        self.assertEqual(self.runs(self.gta), [])
        self.assertEqual(self.queue(self.gta), [])
        self.assertFalse(list((store.path(self.gta.slug, "engage")).glob("runs-*")))

    def test_practice_queues_the_exact_words_and_writes_the_run(self):
        it = self.add(self.gta, self.gitem("source?", tags=["question", "asks_source"]))[0]
        lines = RU.on_items(self.cfg, self.gta, [it], NOW)
        self.assertIn("1 practice", lines[0])
        run = self.runs(self.gta)[0]
        self.assertEqual((run["rule"], run["result"], run["to"]), (self.rid, "practice", "instagram:111"))
        self.assertTrue(run["text"]["reply_public"].startswith("@sam "))
        q = self.queue(self.gta)
        self.assertEqual(len(q), 1)
        self.assertEqual((q[0]["kind"], q[0]["key"], q[0]["force_practice"]),
                         ("reply_public", f"rp:instagram:{it['platform_comment_id']}", True))
        self.assertEqual(q[0]["text"], run["text"]["reply_public"])
        self.assertEqual(q[0]["origin"]["rule"], self.rid)
        self.assertEqual(SS.get_item(self.gta, it["id"])["auto"]["result"], "practice")
        OW.drain(self.cfg, NOW)
        rows = list(OW.rows(self.cfg, self.gta.slug, states=("practice",)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], run["text"]["reply_public"])

    def test_one_run_per_comment(self):
        it = self.add(self.gta, self.gitem("source?", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        RU.on_items(self.cfg, self.gta, [it], NOW + timedelta(minutes=1))
        self.assertEqual(len(self.runs(self.gta)), 1)

    def test_one_message_per_person_a_day_across_rules(self):
        other = self.save(self.gta, base_rule(name="When", trigger={"intents": ["asks_when"], "keywords": ["when"]},
                                              action={"public": {"on": True, "variants": [
                                                  "{mention} GTA 6 comes out {gta6_date}.",
                                                  "{mention} it is {days_to_gta6} away.",
                                                  "{mention} {gta6_date} is the release day."]}}))
        a = self.add(self.gta, self.gitem("source?", tags=["asks_source"], at=NOW - timedelta(minutes=50)))[0]
        RU.on_items(self.cfg, self.gta, [a], NOW - timedelta(minutes=49))
        b = self.add(self.gta, self.gitem("when does it come out", tags=["asks_when"]))[0]
        RU.on_items(self.cfg, self.gta, [b], NOW)
        runs = self.runs(self.gta)
        self.assertEqual([(r["rule"], r["result"], r["why"]) for r in runs][1], (other, "skipped", "contact_24h"))

    def test_one_per_person_per_rule(self):
        a = self.add(self.gta, self.gitem("source?", tags=["asks_source"], at=NOW - timedelta(days=2)))[0]
        RU.on_items(self.cfg, self.gta, [a], NOW - timedelta(days=2))
        b = self.add(self.gta, self.gitem("source again", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [b], NOW)
        self.assertEqual(self.runs(self.gta)[1]["why"], "person_days")

    def test_variants_rotate(self):
        used = []
        for i in range(6):
            t = NOW + timedelta(minutes=20 * i)
            it = self.add(self.gta, self.gitem("source?", who=f"9{i}", name=f"p{i}", tags=["asks_source"],
                                               at=t - timedelta(minutes=1)), now=t)[0]
            RU.on_items(self.cfg, self.gta, [it], t)
            run = self.runs(self.gta)[-1]
            used.append(next(a["variant"] for a in run["actions"] if a["kind"] == "reply_public"))
        self.assertTrue(all(x != y for x, y in zip(used, used[1:])), used)
        self.assertGreater(len(set(used)), 1)

    def test_the_same_words_never_twice_a_day(self):
        rid = self.save(self.gta, base_rule(name="Only host", trigger={"keywords": ["whose"], "intents": []},
                                            action={"public": {"on": True, "variants": [
                                                "{mention} this is {host}.", "{mention} that is {speaker} here.",
                                                "{mention} {speaker} is on {host}."]}}))
        a = self.add(self.gta, self.gitem("whose clip", who="1", name="sam"))[0]
        RU.on_items(self.cfg, self.gta, [a], NOW)
        OW.drain(self.cfg, NOW)
        b = self.add(self.gta, self.gitem("whose clip is it", who="2", name="sam",
                                          at=NOW + timedelta(minutes=9)))[0]
        RU.on_items(self.cfg, self.gta, [b], NOW + timedelta(minutes=10))
        last = [r for r in self.runs(self.gta) if r["rule"] == rid][-1]
        self.assertEqual((last["result"], last["why"]), ("skipped", "repeat"))

    def test_quiet_hours_hold_until_morning(self):
        night = datetime(2031, 7, 8, 8, 0, tzinfo=timezone.utc)          # 02:00 in Denver
        it = self.add(self.gta, self.gitem("source?", tags=["asks_source"], at=night - timedelta(minutes=2)),
                      now=night)[0]
        RU.on_items(self.cfg, self.gta, [it], night)
        run = self.runs(self.gta)[0]
        act = run["actions"][0]
        self.assertEqual(act["would_at"], "2031-07-08T13:00:00+00:00")           # 07:00 in Denver
        self.assertIn("quiet hours", run["note"])
        self.assertEqual(self.queue(self.gta)[0]["not_before"], "2031-07-08T13:00:00+00:00")

    def test_stop_never_touched(self):
        SS.stop_contact(self.gta, "instagram:111", NOW, why="stop")
        it = self.add(self.gta, self.gitem("source?", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        self.assertEqual((self.runs(self.gta)[0]["result"], self.runs(self.gta)[0]["why"]), ("skipped", "stop"))
        it2 = self.add(self.gta, self.gitem("source? stop", who="5", tags=["asks_source", "stop"]))[0]
        RU.on_items(self.cfg, self.gta, [it2], NOW)
        self.assertEqual(self.runs(self.gta)[1]["why"], "stop")
        self.assertEqual(self.queue(self.gta), [])

    def test_missing_fact_skips(self):
        self.w.gta_slot(url="", pick=None)
        p = store.path(self.gta.slug, f"{DAY}.json")
        bd = store.read(p, {})
        bd["slots"]["08:45"].update(host="", source_title="")
        store.write(p, bd)
        it = self.add(self.gta, self.gitem("source?", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        run = self.runs(self.gta)[0]
        self.assertEqual((run["result"], run["why"]), ("skipped", "no_fact"))
        self.assertEqual(self.queue(self.gta), [])

    def test_never_ours_replies_or_hidden(self):
        mine = self.gitem("source?", who="ME", tags=["asks_source"])
        mine["author"]["is_us"] = True
        reply = self.gitem("source?", who="3", tags=["asks_source"])
        reply["parent_id"] = "C1"
        hidden = self.gitem("source?", who="4", tags=["asks_source"])
        hidden["hidden"] = True
        self.add(self.gta, mine, reply, hidden)
        RU.on_items(self.cfg, self.gta, [mine, reply, hidden], NOW)
        self.assertEqual(self.runs(self.gta), [])

    def test_youtube_only_a_suggestion(self):
        self.save(self.gta, base_rule(name="Yt", platforms=["youtube"], trigger={"keywords": ["credit"], "intents": []},
                                      action={"public": {"on": True, "variants": [
                                          "{mention} credit to {host}.", "{mention} by {host}, {source_title}.",
                                          "{mention} the video is {source_title}."]}}))
        it = self.add(self.gta, self.gitem("credit?", platform="youtube",
                                           post=self.w.gta_post("youtube_shorts", pid="yt1")))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        self.assertEqual(self.queue(self.gta), [])
        got = SS.get_item(self.gta, it["id"])["suggested_reply"]
        self.assertTrue(got["text"].startswith("@sam "))
        run = self.runs(self.gta)[0]
        self.assertEqual((run["result"], run["why"]), ("skipped", "youtube"))

    def test_message_rule_answers_with_a_message(self):
        rid = self.save(self.gta, base_rule(name="DM source", trigger={"on": "message", "intents": ["asks_source"],
                                                                       "keywords": []},
                                            action={"public": {"on": False, "variants": []},
                                                    "private": {"on": True, "variants": [
                                                        "Full video from {host}: {source_url}"]}}))
        dm = self.gitem("where is this from", tags=["asks_source"], kind="dm")
        dm["id"], dm["thread_id"] = "igm:M1", "igt:T1"
        self.add(self.gta, dm)
        RU.on_items(self.cfg, self.gta, [dm], NOW)
        q = self.queue(self.gta)[0]
        self.assertEqual((q["kind"], q["key"]), ("dm", f"dm:instagram:T1:{dm['platform_comment_id']}"))
        self.assertEqual(q["text"], "Automated reply: Full video from MrBossFTW: " + GTA_URL + "&t=287s")
        self.assertEqual(self.runs(self.gta)[0]["rule"], rid)


class Links(Base):
    def test_a_link_only_answers_a_source_ask(self):
        self.practice(self.gta)
        rid = self.save(self.gta, base_rule(name="Stream", trigger={"intents": ["asks_who", "asks_source"],
                                                                    "keywords": []},
                                            action={"private": {"on": True, "variants": [
                                                "Full video from {host}: {source_url}"]}}))
        who = self.add(self.gta, self.gitem("who is he", who="31", tags=["asks_who"]))[0]
        src = self.add(self.gta, self.gitem("source?", who="32", name="lee", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [who, src], NOW)
        by = {r["to"]: r for r in self.runs(self.gta) if r["rule"] == rid}
        acts = {a["kind"]: a for a in by["instagram:31"]["actions"]}
        self.assertEqual((acts["reply_private"]["ok"], acts["reply_private"]["code"]), (False, "text"))
        self.assertTrue(acts["reply_public"]["ok"])
        acts = {a["kind"]: a for a in by["instagram:32"]["actions"]}
        self.assertTrue(acts["reply_private"]["ok"])
        q = [x for x in self.queue(self.gta) if x["kind"] == "reply_private"]
        self.assertEqual([x["target"]["intent"] for x in q], ["asks_source"])


class People(Base):
    def test_disclosure_once_per_person(self):
        self.practice(self.politics)
        rid = self.save(self.politics, base_rule(trigger={"intents": ["asks_source", "asks_full"]}, action={
            "public": {"on": False, "variants": []},
            "private": {"on": True, "variants": ["Here is the full {kind}: {source_url}"]}}),
            now=NOW - timedelta(days=10))
        self.w.politics_job(slot="19:00", slug="wright-again", pid="LIG2")
        first = NOW - timedelta(days=8)          # past the 7 days between private replies to one person
        a = self.add(self.politics, self.w.item(self.politics, self.w.politics_post(), "source?", tags=["asks_source"],
                                              at=first), now=first)[0]
        RU.on_items(self.cfg, self.politics, [a], first + timedelta(minutes=1))
        b = self.add(self.politics, self.w.item(self.politics, self.w.politics_post("19:00", pid="LIG2"), "full video?",
                                              tags=["asks_full"]))[0]
        RU.on_items(self.cfg, self.politics, [b], NOW)
        texts = [r["text"]["reply_private"] for r in self.runs(self.politics) if r["rule"] == rid]
        self.assertTrue(texts[0].startswith("Automated reply: Here is the full interview: https://"), texts)
        self.assertEqual(texts[1], "Here is the full interview: https://abcnews.example/this-week-full")
        q = self.queue(self.politics)
        self.assertEqual([x["kind"] for x in q], ["reply_private", "reply_private"])
        self.assertIn("only app testers", " ".join(r["actions"][0]["why"] for r in self.runs(self.politics)))

    def test_testers_mode_practises_for_everyone_else(self):
        self.grant(self.gta)
        self.warm(self.gta)
        self.set(self.gta, {}, {"testers": [{"platform": "instagram", "id": "111", "name": "Andrew"}]})
        self.signed_live(self.gta, mode="testers", account_mode="testers")
        a = self.add(self.gta, self.gitem("source?", who="111", tags=["asks_source"]))[0]
        b = self.add(self.gta, self.gitem("source?", who="222", name="kim", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [a, b], NOW)
        got = {r["to"]: (r["result"], r["why"]) for r in self.runs(self.gta)}
        self.assertEqual(got["instagram:111"][0], "queued")
        self.assertEqual(got["instagram:222"][0], "practice")
        self.assertIn("not a tester", got["instagram:222"][1])
        q = {x["target"]["to"]: x["force_practice"] for x in self.queue(self.gta)}
        self.assertEqual(q, {"instagram:111": False, "instagram:222": True})

    def test_warm_up_practises(self):
        self.grant(self.gta)
        self.signed_live(self.gta)
        it = self.add(self.gta, self.gitem("source?", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        run = self.runs(self.gta)[0]
        self.assertEqual(run["result"], "practice")
        self.assertIn("warming up", run["why"])

    def test_live_goes_through_the_fake_executor(self):
        self.grant(self.gta)
        self.warm(self.gta)
        rid = self.signed_live(self.gta)
        eng = self.inject_engage()
        it = self.add(self.gta, self.gitem("source?", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        self.assertEqual(self.runs(self.gta)[0]["result"], "queued")
        OW.drain(self.cfg, NOW)
        self.assertEqual(len(eng.calls), 1)
        self.assertEqual(eng.calls[0]["origin"]["rule"], rid)
        stats = RU.analytics(self.cfg, self.gta, NOW)[rid]
        self.assertEqual((stats["h24"]["triggered"], stats["h24"]["queued"], stats["h24"]["sent"]), (1, 1, 1))

    def test_still_signed(self):
        rid = self.signed_live(self.gta)
        r = RU.get(self.gta, rid)
        act = {"origin": {"by": "rule", "rule": rid}, "signed": {"hash": RU.rule_hash(r)}, "force_practice": False}
        self.assertEqual(RU.still_signed(self.cfg, self.gta, act), "")
        self.assertEqual(RU.still_signed(self.cfg, self.gta, dict(act, force_practice=True)), "")
        self.assertIn("changed after this reply", RU.still_signed(self.cfg, self.gta, dict(act, signed={"hash": "0"})))
        RU.save(self.cfg, self.gta, {**base_rule(limits={"per_hour": 2}), "id": rid}, "Andrew", page=True, now=NOW)
        self.assertIn("practice", RU.still_signed(self.cfg, self.gta, act))
        RU.set_mode(self.cfg, self.gta, rid, "paused", "Andrew", now=NOW)
        self.assertIn("paused", RU.still_signed(self.cfg, self.gta, act))


class Part2(Base):
    def setUp(self):
        super().setUp()
        self.practice(self.gta)
        RU.seed(self.cfg, self.gta, "Andrew", now=NOW - timedelta(days=3))
        self.s2 = self.w.gta_slot(slot="12:00", ig="IG2", fb="FB2", slug="part-two")
        self.rec = {"post_id": "IG2", "url": "https://www.instagram.com/reel/IG2/", "provider": "direct"}

    def asked(self, at=NOW - timedelta(hours=2)):
        it = self.add(self.gta, self.gitem("part 2 please", tags=["asks_part2"], at=at), now=at)[0]
        RU.on_items(self.cfg, self.gta, [it], at + timedelta(minutes=1))
        return it

    def test_waitlist_then_one_note(self):
        it = self.asked()
        run = self.runs(self.gta)[0]
        self.assertEqual((run["rule"], run["part2"], run["source"]), ("r-part2", True, "yt:LIugtEjZl_8"))
        self.assertEqual([w["item"] for w in SS.waitlist(self.gta, "yt:LIugtEjZl_8", NOW)], [it["id"]])
        self.assertEqual([q["kind"] for q in self.queue(self.gta)], ["reply_public"])     # no private reply spent
        got = RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec, NOW)
        self.assertIn("part 2 noted for 1", got)
        q = [x for x in self.queue(self.gta) if x["key"].endswith(":part2")]
        self.assertEqual(len(q), 1)
        self.assertIn(q[0]["text"], [v.replace("{mention}", "@sam") for v in RU.PART2_PUBLIC])
        self.assertIsNone(RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec,
                                       NOW + timedelta(minutes=5)))
        self.assertEqual(len([x for x in self.queue(self.gta) if x["key"].endswith(":part2")]), 1)
        self.assertIsNone(RU.on_posted(self.cfg, self.gta, DAY, self.s2, "facebook_reels", self.rec, NOW))

    def test_private_link_when_messages_are_open(self):
        self.set(self.gta, {}, {"messages_access": "advanced"})
        it = self.asked()
        RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec, NOW)
        q = [x for x in self.queue(self.gta) if x["kind"] == "reply_private"]
        self.assertEqual(q[0]["key"], f"pr:instagram:{it['platform_comment_id']}")
        link = "https://www.instagram.com/reel/IG2/"
        self.assertIn(q[0]["text"], ["Automated reply: " + v.replace("{part2_url}", link) for v in RU.PART2_PRIVATE])
        self.assertEqual(q[0]["target"]["intent"], "part2")

    def test_not_after_six_days_and_not_the_same_clip(self):
        self.asked(at=NOW - timedelta(days=7))
        self.assertIsNone(RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec, NOW))
        s1 = (store.read(store.path(self.gta.slug, f"{DAY}.json"), {}))["slots"]["08:45"]
        self.asked()
        self.assertIsNone(RU.on_posted(self.cfg, self.gta, DAY, s1, "instagram_reels", self.rec, NOW))

    def test_another_source_waits(self):
        self.asked()
        other = self.w.gta_slot(slot="15:00", ig="IG3", fb="FB3", url="https://www.youtube.com/watch?v=AAAAAAAAAAA")
        self.assertIsNone(RU.on_posted(self.cfg, self.gta, DAY, other, "instagram_reels", self.rec, NOW))

    # ---------------------------------------------------------------------- one note per waiter, whatever happens
    def live_part2(self, messages=True):
        if messages:
            self.set(self.gta, {}, {"messages_access": "advanced"})
        self.grant(self.gta)
        self.warm(self.gta)
        r = RU.get(self.gta, "r-part2")
        RU.sign(self.cfg, self.gta, "r-part2", RU.rule_hash(r), "Andrew", page=True, now=NOW - timedelta(days=2))
        self.set(self.gta, {"automations": "on"})
        RU.set_mode(self.cfg, self.gta, "r-part2", "live", "Andrew", page=True, now=NOW - timedelta(days=1))
        self.s3 = self.w.gta_slot(slot="15:00", ig="IG3", fb="FB3", slug="part-three")
        self.rec3 = {"post_id": "IG3", "url": "https://www.instagram.com/reel/IG3/", "provider": "direct"}

    def notes(self):
        return [(q["kind"], q["key"]) for q in self.queue(self.gta) if (q.get("target") or {}).get("intent") == "part2"]

    def test_a_lost_waitlist_mark_never_sends_a_second_note(self):
        self.live_part2()
        it = self.asked()
        real = SS.waitlist_mark
        calls = {"n": 0}

        def flaky(*a, **k):                      # the write fails (or the job dies) right after the queue
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("killed between the queue and the waitlist mark")
            return real(*a, **k)
        SS.waitlist_mark = flaky
        self.addCleanup(setattr, SS, "waitlist_mark", real)
        RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec, NOW)
        self.assertEqual(self.notes(), [("reply_private", f"pr:instagram:{it['platform_comment_id']}")])
        self.assertIsNone(RU.on_posted(self.cfg, self.gta, DAY, self.s3, "instagram_reels", self.rec3,
                                       NOW + timedelta(minutes=20)))
        self.assertEqual(len(self.notes()), 1)                     # never the public note on top of the private

    def test_two_jobs_at_once_send_one_note(self):
        """The social tick's sweep reads the waitlist; the poster's follow-up for another clip runs whole; the
        sweep goes on with what it read. The claim under the lock keeps it to one note."""
        self.live_part2()
        self.asked()
        real = RU._hist
        state = {"done": False}

        def hist_then_other(a, now):
            if not state["done"]:
                state["done"] = True
                RU.on_posted(self.cfg, self.gta, DAY, self.s3, "instagram_reels", self.rec3, now)
            return real(a, now)
        RU._hist = hist_then_other
        self.addCleanup(setattr, RU, "_hist", real)
        self.assertIsNone(RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec, NOW))
        self.assertEqual([k for k, _ in self.notes()], ["reply_private"])
        sent = [w.get("sent") for w in (SS.read(self.gta, "waitlist", {}) or {})["waitlist"]["yt:LIugtEjZl_8"]]
        self.assertEqual(sent, [self.queue(self.gta)[-1]["id"]])   # the one note's id, not overwritten

    def test_a_claim_is_given_back_when_nothing_was_queued(self):
        self.live_part2()
        it = self.asked()
        real = RU._outward_check

        def boom(*a, **k):
            raise RuntimeError("the check fell over")
        RU._outward_check = boom
        self.addCleanup(setattr, RU, "_outward_check", real)
        with self.assertRaises(RuntimeError):
            RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec, NOW)
        self.assertEqual([w["item"] for w in SS.waitlist(self.gta, "yt:LIugtEjZl_8", NOW)], [it["id"]])
        RU._outward_check = real
        self.assertIn("part 2 noted for 1", RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels",
                                                         self.rec, NOW + timedelta(minutes=5)))
        self.assertEqual(len(self.notes()), 1)

    # ---------------------------------------------------------------------- the words rotate, the day limit holds
    def test_part2_words_rotate_and_pass_the_checks(self):
        for vs, kind in ((RU.PART2_PUBLIC, "reply_public"), (RU.PART2_PRIVATE, "reply_private")):
            self.assertGreaterEqual(len(vs), 2)
            for i, x in enumerate(vs):
                filled = x.replace("{mention}", "@sam").replace("{part2_url}", "https://www.instagram.com/reel/IG2/")
                for maker in ("gta", "politics"):
                    self.assertEqual(RU.rendered_problem(filled, kind, maker, url_ok=True), "", filled)
                for y in vs[i + 1:]:
                    self.assertLess(RU.jaccard(x, y), RU.JACCARD_MAX, (x, y))
        self.assertGreaterEqual(len(RU.PART2_PUBLIC), 3)
        for i in range(4):
            self.add(self.gta, self.gitem("part 2 please", who=f"7{i}", name=f"q{i}", tags=["asks_part2"],
                                          at=NOW - timedelta(hours=3)), now=NOW - timedelta(hours=3))
        RU.on_items(self.cfg, self.gta, [SS.get_item(self.gta, i["id"]) for i in SS.items(self.gta)],
                    NOW - timedelta(hours=3) + timedelta(minutes=1))
        RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec, NOW)
        notes = [r for r in self.runs(self.gta) if r.get("on") == "part2" and r.get("result") in ("queued", "practice")]
        self.assertEqual(len(notes), 4)
        bodies = [r["text"]["reply_public"].split(" ", 1)[1] for r in notes]
        self.assertGreater(len(set(bodies)), 1)
        variants = [r["actions"][0]["variant"] for r in notes]
        self.assertTrue(all(isinstance(v, int) for v in variants))
        self.assertTrue(all(a != b for a, b in zip(variants, variants[1:])), variants)   # never the one used last

    def test_the_words_are_signed(self):
        r = RU.get(self.gta, "r-part2")
        self.assertTrue((r["action"].get("part2") or {}).get("on"))
        before = RU.rule_hash(r)
        old = RU.PART2_PUBLIC
        RU.PART2_PUBLIC = old + ("{mention} one more wording.",)
        self.addCleanup(setattr, RU, "PART2_PUBLIC", old)
        self.assertNotEqual(RU.rule_hash(r), before)               # new factory words need a new signature
        RU.PART2_PUBLIC = old
        words = RU.sign_words(self.gta, r)
        for v in RU.PART2_PUBLIC + RU.PART2_PRIVATE:
            self.assertIn(v, words)

    def test_one_automated_message_a_day_holds_the_note(self):
        t0 = NOW - timedelta(hours=30)
        it = self.add(self.gta, self.gitem("part 2 please", tags=["asks_part2"], at=t0), now=t0)[0]
        RU.on_items(self.cfg, self.gta, [it], t0 + timedelta(minutes=1))
        t1 = NOW - timedelta(hours=2)                              # another rule answers sam's other question
        other = self.add(self.gta, self.gitem("whose stream is this", tags=["asks_who", "question"], at=t1), now=t1)[0]
        RU.on_items(self.cfg, self.gta, [other], t1 + timedelta(minutes=1))
        self.assertEqual(len([q for q in self.queue(self.gta) if q["target"]["to"] == "instagram:111"]), 2)
        got = RU.on_posted(self.cfg, self.gta, DAY, self.s2, "instagram_reels", self.rec, NOW)
        self.assertIn("1 more wait", got)
        self.assertEqual(self.notes(), [])
        self.assertEqual([w["item"] for w in SS.waitlist(self.gta, "yt:LIugtEjZl_8", NOW)], [it["id"]])
        self.assertEqual(RU.part2_sweep(self.cfg, self.gta, NOW + timedelta(hours=1)), ["Gaming: 1 more wait "
                         "for the rule's hour or day limit, or their one message a day"])
        got = RU.part2_sweep(self.cfg, self.gta, NOW + timedelta(hours=23))
        self.assertIn("part 2 noted for 1", got[0])
        self.assertEqual(len(self.notes()), 1)


class SelfPause(Base):
    def test_two_stops_pause_the_rule(self):
        rid = self.signed_live(self.gta)
        for who in ("A", "B"):
            SS.append_run(self.gta, {"at": (NOW - timedelta(days=1)).isoformat(), "rule": rid, "item": f"ig:{who}",
                                     "result": "queued", "to": f"instagram:{who}"}, NOW)
        SS.stop_contact(self.gta, "instagram:A", NOW - timedelta(hours=12), why="stop")
        self.assertEqual(RU.self_pause(self.cfg, self.gta, NOW), [])
        bot = self.gitem("are you a bot", who="B", at=NOW - timedelta(hours=10))
        bot["parent_id"] = "C1"
        self.add(self.gta, bot)
        lines = RU.self_pause(self.cfg, self.gta, NOW)
        self.assertIn("back to practice by itself", lines[0])
        r = RU.get(self.gta, rid)
        self.assertEqual(r["mode"], "practice")
        self.assertEqual(r["flags"][0]["key"], "self_pause")
        self.assertTrue(any(f[0] is False and "by itself" in f[1] for f in RU.doctor(self.cfg, NOW)))

    def test_a_hidden_reply_pauses_the_rule(self):
        rid = self.signed_live(self.gta)
        SS.append_run(self.gta, {"at": (NOW - timedelta(hours=5)).isoformat(), "rule": rid, "item": "ig:X",
                                 "result": "queued", "to": "instagram:X"}, NOW)
        OW.record(self.cfg, self.gta, "instagram", "reply_public", state="done", origin={"by": "rule", "rule": rid},
                  result={"id": "R77"}, now=NOW - timedelta(hours=5))
        ours = self.gitem("@x this is MrBossFTW", who="US", at=NOW - timedelta(hours=4))
        ours.update(platform_comment_id="R77", hidden=True)
        self.add(self.gta, ours)
        RU.self_pause(self.cfg, self.gta, NOW)
        self.assertEqual(RU.get(self.gta, rid)["mode"], "practice")


class PureTools(Base):
    def setUp(self):
        super().setUp()
        RU.seed(self.cfg, self.gta, "Andrew", now=NOW - timedelta(days=3))

    def snapshot(self):
        out = {}
        for p in sorted(store.path(self.gta.slug).rglob("*")):
            if p.is_file() and not p.name.endswith(".lock"):
                out[str(p)] = p.read_bytes()
        return out

    def test_replay_is_pure_and_shows_who_when_and_why(self):
        for i, (text, tags) in enumerate((("whose stream is this", ["asks_who"]), ("part 2 please", ["asks_part2"]),
                                         ("when does gta 6 come out", ["asks_when"]), ("nice", ["praise"]))):
            t = NOW - timedelta(days=1, hours=i)
            self.add(self.gta, self.gitem(text, who=str(i), name=f"p{i}", tags=tags, at=t), now=t)
        night = datetime(2031, 7, 6, 8, 0, tzinfo=timezone.utc)
        self.add(self.gta, self.gitem("source?", who="9", name="owl", tags=["asks_source"], at=night), now=night)
        before = self.snapshot()
        got = RU.replay(self.cfg, self.gta, 7, now=NOW)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.queue(self.gta), [])
        t = got["tally"]
        self.assertEqual((t["comments"], t["matched"], t["would_send"]), (5, 4, 4))
        owl = next(r for r in got["rows"] if r["name"] == "owl")
        self.assertEqual(owl["actions"][0]["would_at"], "2031-07-06T13:00:00+00:00")
        self.assertIn("quiet hours", owl["note"])
        self.assertEqual(owl["comment"], "source?")
        self.assertTrue(owl["text"]["reply_public"].startswith("@owl "))

    def test_replay_of_an_unsaved_draft(self):
        self.add(self.gta, self.gitem("credit please", tags=[], at=NOW - timedelta(hours=3)))
        draft = base_rule(name="Credit", trigger={"intents": [], "keywords": ["credit"]})
        got = RU.replay(self.cfg, self.gta, 7, rule=draft, now=NOW)
        self.assertEqual(got["tally"]["would_send"], 1)
        self.assertEqual(len(RU.rules(self.gta)), 3)

    def test_try_it_is_pure(self):
        before = self.snapshot()
        got = RU.try_text(self.cfg, self.gta, "whose stream is this?", "instagram", now=NOW)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(got["run"]["rule"], "r-stream")
        self.assertIn("asks_who", got["tags"])
        self.assertTrue(got["run"]["text"]["reply_public"].startswith("@someone "))
        self.assertEqual(got["post"]["platform_id"], "IG1")
        miss = RU.try_text(self.cfg, self.gta, "nice edit", "instagram", now=NOW)
        self.assertIsNone(miss["run"])


class Review1020(Base):
    """The 10.20 review: each finding, as it was reproduced, now held."""
    NIGHT = datetime(2031, 7, 8, 8, 0, tzinfo=timezone.utc)          # 02:00 in Denver: quiet hours till 07:00

    def fake_engage(self):
        return self.inject_engage()

    def queued_at_night(self, rule=None, mode="live", account_mode="on"):
        self.grant(self.gta)
        self.warm(self.gta)
        rid = self.signed_live(self.gta, rule, mode=mode, account_mode=account_mode)
        eng = self.fake_engage()
        it = self.add(self.gta, self.gitem("source?", tags=["asks_source"], at=self.NIGHT - timedelta(minutes=2)),
                      now=self.NIGHT)[0]
        RU.on_items(self.cfg, self.gta, [it], self.NIGHT)
        q = self.queue(self.gta)
        self.assertEqual([(x["force_practice"], x["not_before"]) for x in q], [(False, "2031-07-08T13:00:00+00:00")])
        return rid, eng, it

    def morning(self):
        OW.drain(self.cfg, self.NIGHT + timedelta(hours=5, minutes=5))

    # 1. back to practice, by hand or by the self-pause: what was queued live does not go
    def test_back_to_practice_stops_queued_live_replies(self):
        rid, eng, _ = self.queued_at_night()
        RU.set_mode(self.cfg, self.gta, rid, "practice", "Andrew", now=self.NIGHT + timedelta(minutes=5))
        self.morning()
        self.assertEqual(eng.calls, [])
        why = [r.get("why") for r in OW.rows(self.cfg, self.gta.slug, kinds=("reply_public",), states=("skipped",))]
        self.assertTrue(any("practice now" in str(w) for w in why), why)

    def test_self_pause_stops_queued_live_replies(self):
        rid, eng, _ = self.queued_at_night()
        with SS.edit(self.gta, "rules") as d:           # what self_pause writes
            r = d["rules"][rid]
            r["mode"] = "practice"
            r["flags"] = [{"key": "self_pause", "at": (self.NIGHT + timedelta(hours=1)).isoformat(), "why": "x"}]
        self.morning()
        self.assertEqual(eng.calls, [])

    def test_switched_off_then_on_again_still_drops_the_old_queue(self):
        rid, eng, _ = self.queued_at_night()
        RU.set_mode(self.cfg, self.gta, rid, "practice", "Andrew", now=self.NIGHT + timedelta(minutes=5))
        RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=True, now=self.NIGHT + timedelta(minutes=6))
        self.morning()
        self.assertEqual(eng.calls, [])

    def test_testers_mode_sends_only_to_testers_at_send_time(self):
        self.set(self.gta, {}, {"testers": [{"platform": "instagram", "id": "111", "name": "Andrew"}]})
        rid, eng, it = self.queued_at_night(mode="testers", account_mode="testers")
        self.set(self.gta, {}, {"testers": []})          # he took the tester off before the morning
        self.morning()
        self.assertEqual(eng.calls, [])

    def test_live_still_goes_when_nothing_changed(self):
        rid, eng, _ = self.queued_at_night()
        self.morning()
        self.assertEqual(len(eng.calls), 1)

    # 7. answered by hand before 07:00: the automated reply does not go as well
    def test_answered_by_hand_meanwhile(self):
        rid, eng, it = self.queued_at_night()
        SS.add_reply(self.gta, it["id"], {"id": "MAN1", "by": "us", "text": "it is MrBossFTW",
                                          "at": self.NIGHT.isoformat(), "action": "oa-manual"})
        SS.set_state(self.gta, it["id"], "answered", "click", self.NIGHT + timedelta(hours=4), answered="x")
        self.morning()
        self.assertEqual(eng.calls, [])

    def test_marked_done_or_hidden_meanwhile(self):
        rid, eng, it = self.queued_at_night()
        SS.set_state(self.gta, it["id"], "hidden", "click", self.NIGHT + timedelta(hours=1))
        self.morning()
        self.assertEqual(eng.calls, [])

    # 4. edited while paused: the new words practise 24 hours before Live
    def test_paused_edit_needs_new_practice(self):
        rid = self.signed_live(self.gta)
        RU.set_mode(self.cfg, self.gta, rid, "paused", "Andrew", now=NOW)
        new = base_rule(action={"public": {"on": True, "variants": [
            "{mention} brand new words from {host}.", "{mention} another new way, {source_title}.",
            "{mention} a third fresh line by {host} on {source_title}."]}})
        RU.save(self.cfg, self.gta, {**new, "id": rid}, "Andrew", page=True, now=NOW + timedelta(minutes=1))
        r = RU.get(self.gta, rid)
        RU.sign(self.cfg, self.gta, rid, RU.rule_hash(r), "Andrew", page=True, now=NOW + timedelta(minutes=2))
        with self.assertRaises(SystemExit) as e:
            RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=True, now=NOW + timedelta(minutes=3))
        self.assertIn("24 hours", str(e.exception))
        with self.assertRaises(SystemExit):                 # paused time is no practice either
            RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=True, now=NOW + timedelta(days=3))
        RU.set_mode(self.cfg, self.gta, rid, "practice", "Andrew", now=NOW + timedelta(days=3))
        RU.set_mode(self.cfg, self.gta, rid, "live", "Andrew", page=True, now=NOW + timedelta(days=4, minutes=1))
        self.assertEqual(RU.get(self.gta, rid)["mode"], "live")

    def test_paused_after_practice_keeps_the_hours_it_practised(self):
        rid = self.save(self.gta, now=NOW - timedelta(days=3))
        RU.sign(self.cfg, self.gta, rid, RU.rule_hash(RU.get(self.gta, rid)), "Andrew", page=True, now=NOW)
        RU.set_mode(self.cfg, self.gta, rid, "paused", "Andrew", now=NOW - timedelta(days=1))
        self.assertGreaterEqual(int(RU._practised_h(RU.get(self.gta, rid), NOW + timedelta(days=5))), 48)
        self.assertLess(RU._practised_h(RU.get(self.gta, rid), NOW + timedelta(days=5)), 49)

    # 3. the disclosure: only automated history counts; a manual private reply never stands in for it
    def test_disclosure_after_a_manual_private_reply(self):
        self.grant(self.politics)
        self.warm(self.politics)
        self.set(self.politics, {}, {"messages_access": "advanced"})
        SS.touch_contact(self.politics, "instagram:111", NOW - timedelta(days=60), outbound=True, auto=False,
                         private_reply="MANUAL1")
        self.signed_live(self.politics, base_rule(trigger={"intents": ["asks_source", "asks_full"]}, action={
            "public": {"on": False, "variants": []},
            "private": {"on": True, "variants": ["Here is the full {kind}: {source_url}"]}}))
        it = self.add(self.politics, self.w.item(self.politics, self.w.politics_post(), "source?", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.politics, [it], NOW)
        q = [x for x in self.queue(self.politics) if x["kind"] == "reply_private"]
        self.assertEqual(q[0]["force_practice"], False)
        self.assertTrue(q[0]["text"].startswith("Automated reply: "), q[0]["text"])

    def test_disclosure_again_after_he_wrote_by_hand_or_a_long_gap(self):
        hist = RU._Hist([{"at": (NOW - timedelta(days=10)).isoformat(), "rule": "r", "to": "instagram:9",
                          "result": "practice", "actions": [{"kind": "reply_private", "ok": True}]}])
        self.assertFalse(RU._needs_disclosure(self.cfg, self.gta, "instagram:9", NOW, hist, True, False, {}))
        SS.touch_contact(self.gta, "instagram:9", NOW - timedelta(days=2), outbound=True)    # his own words
        self.assertTrue(RU._needs_disclosure(self.cfg, self.gta, "instagram:9", NOW, hist, True, False, {}))
        old = RU._Hist([dict(hist.rows[0], at=(NOW - timedelta(days=40)).isoformat())])
        self.assertTrue(RU._needs_disclosure(self.cfg, self.gta, "instagram:8", NOW, old, True, False, {}))

    # 2. one private reply per person in 7 days, across every rule and his own clicks
    def test_one_private_reply_per_person_in_7_days_across_rules(self):
        self.practice(self.politics)
        mk = lambda name, intents: base_rule(name=name, trigger={"intents": intents, "keywords": []}, action={  # noqa
            "public": {"on": False, "variants": []},
            "private": {"on": True, "variants": ["Here is the full {kind}: {source_url} from {host}"]}})
        self.save(self.politics, mk("Source", ["asks_source"]))
        self.save(self.politics, mk("Full", ["asks_full"]))
        self.w.politics_job(slot="19:00", slug="wright-again", pid="LIG2")
        t1 = NOW - timedelta(hours=25)
        a = self.add(self.politics, self.w.item(self.politics, self.w.politics_post(), "source?", tags=["asks_source"],
                                              at=t1), now=t1)[0]
        RU.on_items(self.cfg, self.politics, [a], t1 + timedelta(minutes=1))
        b = self.add(self.politics, self.w.item(self.politics, self.w.politics_post("19:00", pid="LIG2"), "full video?",
                                              tags=["asks_full"]))[0]
        RU.on_items(self.cfg, self.politics, [b], NOW)
        runs = self.runs(self.politics)
        self.assertEqual((runs[0]["result"], runs[1]["result"], runs[1]["why"]), ("practice", "skipped", "person_7d"))
        self.assertEqual([x["kind"] for x in self.queue(self.politics)], ["reply_private"])

    def test_his_own_private_reply_counts_too(self):
        self.practice(self.politics)
        self.save(self.politics, base_rule(trigger={"intents": ["asks_source"], "keywords": []}, action={
            "public": {"on": False, "variants": []},
            "private": {"on": True, "variants": ["Here is the full {kind}: {source_url}"]}}))
        OW.record(self.cfg, self.politics, "instagram", "reply_private", state="done", mode="live",
                  origin={"by": "click"}, signed={"what": "click"}, target={"to": "instagram:111"},
                  now=NOW - timedelta(days=2))
        it = self.add(self.politics, self.w.item(self.politics, self.w.politics_post(), "source?", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.politics, [it], NOW)
        self.assertEqual(self.runs(self.politics)[0]["why"], "person_7d")

    def test_private_rules_keep_7_days_between_replies(self):
        with self.assertRaises(SystemExit) as e:
            RU.save(self.cfg, self.politics, base_rule(action={"public": {"on": False, "variants": []}, "private": {
                "on": True, "variants": ["Here is the full {kind}: {source_url}"]}}, limits={"person_days": 2}),
                "Andrew", page=True)
        self.assertIn("7 to 30", str(e.exception))

    # 5. a word alone (no ask) never gets the source link
    def test_keyword_only_match_gets_no_link(self):
        self.practice(self.gta)
        self.save(self.gta, base_rule(name="Stream", trigger={"intents": ["asks_source"], "keywords": ["streamer"]},
                                      action={"public": {"on": False, "variants": []},
                                              "private": {"on": True, "variants": [
                                                  "Full video from {host}: {source_url}"]}}))
        it = self.add(self.gta, self.gitem("that streamer is so annoying", tags=[]))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        self.assertEqual(self.queue(self.gta), [])
        self.assertEqual(self.runs(self.gta)[0]["why"], "text")

    def test_keyword_only_match_takes_a_variant_without_a_link(self):
        self.practice(self.gta)
        self.save(self.gta, base_rule(name="Stream", trigger={"intents": ["asks_source"], "keywords": ["streamer"]},
                                      action={"public": {"on": False, "variants": []},
                                              "private": {"on": True, "variants": [
                                                  "Full video from {host}: {source_url}",
                                                  "That clip is from {host}, {source_title}."]}}))
        it = self.add(self.gta, self.gitem("this streamer again", tags=[]))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        q = self.queue(self.gta)
        self.assertEqual(len(q), 1)
        self.assertNotIn("http", q[0]["text"])
        self.assertEqual(q[0]["target"]["intent"], "")

    # 6 and 16. the filled-in words: hashtags dropped from titles, 300 characters, the Politics neutral
    def test_filled_in_public_reply_is_checked(self):
        self.practice(self.gta)
        p = store.path(self.gta.slug, f"{DAY}.json")
        bd = store.read(p, {})
        bd["slots"]["08:45"]["source_title"] = "GTA 6 TRAILER 2 REACTION #gta6 #shorts " + "x" * 280
        store.write(p, bd)
        self.save(self.gta, base_rule(action={"public": {"on": True, "variants": [
            "{mention} this is {host}, from {source_title}.", "{mention} it is out of {source_title}.",
            "{mention} that clip comes from {host}."]}}))
        it = self.add(self.gta, self.gitem("source?", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        q = self.queue(self.gta)
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["text"], "@sam that clip comes from MrBossFTW.")
        bd["slots"]["08:45"]["host"] = "X" * 300
        store.write(p, bd)
        it2 = self.add(self.gta, self.gitem("source?", who="2", name="lee", tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.gta, [it2], NOW)
        run = self.runs(self.gta)[-1]
        self.assertEqual((run["result"], run["why"]), ("skipped", "text"))
        self.assertIn("characters", run["note"])

    def test_politics_filled_in_words_stay_neutral(self):
        self.assertIn("neutral", RU.rendered_problem("@sam from Senator SLAMS witness", "reply_public", "politics"))
        self.assertIn("exclamation", RU.rendered_problem("@sam from Hearing!", "reply_public", "politics"))
        self.assertEqual(RU.rendered_problem("@sam from Hearing!", "reply_public", "gta"), "")
        self.assertIn("hashtag", RU.rendered_problem("@sam from #gta6", "reply_public", "gta"))

    # 8. Part 2 notes keep the rule's own signed limits; the rest wait for the sweep
    def test_part2_notes_keep_the_rule_limits(self):
        self.practice(self.gta)
        RU.seed(self.cfg, self.gta, "Andrew", now=NOW - timedelta(days=3))
        s2 = self.w.gta_slot(slot="12:00", ig="IG2", fb="FB2", slug="part-two")
        at = NOW - timedelta(hours=30)
        for i in range(4):
            t = at + timedelta(hours=7 * i)
            it = self.add(self.gta, self.gitem("part 2 please", who=f"7{i}", name=f"q{i}", tags=["asks_part2"],
                                               at=t), now=t)[0]
            RU.on_items(self.cfg, self.gta, [it], t + timedelta(minutes=1))
        p = store.path(self.gta.slug, "engage", "rules.json")
        d = store.read(p, {})
        d["rules"]["r-part2"]["limits"] = {"per_hour": 1, "per_day": 1, "person_days": 7}
        store.write(p, d)
        later = NOW + timedelta(days=1, hours=2)
        got = RU.on_posted(self.cfg, self.gta, DAY, s2, "instagram_reels",
                           {"post_id": "IG2", "url": "https://www.instagram.com/reel/IG2/"}, later)
        notes = lambda: [x for x in self.queue(self.gta) if x["key"].endswith(":part2")]  # noqa: E731
        self.assertEqual(len(notes()), 1)
        self.assertIn("3 more wait", got)
        self.assertEqual(len(SS.waitlist(self.gta, "yt:LIugtEjZl_8", later)), 3)
        RU.on_items(self.cfg, self.gta, [], later + timedelta(hours=2))          # the next poll: within the day
        self.assertEqual(len(notes()), 1)
        RU.on_items(self.cfg, self.gta, [], later + timedelta(days=1, minutes=5))   # a day on: one more
        self.assertEqual(len(notes()), 2)

    # 12. the starters answer what was asked, with the Inbox classifier's own tags
    def starter_run(self, acct, text, tags):
        it = self.w.item(acct, self.w.gta_post() if acct is self.gta else self.w.politics_post(), text, tags=tags)
        run = RU.decide(self.cfg, acct, it, RU.rules(acct), NOW, RU._Hist([]), {})
        return run and run.get("rule")

    def test_starters_are_anchored(self):
        self.practice(self.gta)
        self.practice(self.politics)
        RU.seed(self.cfg, self.gta, "Andrew", now=NOW - timedelta(days=3))
        RU.seed(self.cfg, self.politics, "Andrew", now=NOW - timedelta(days=3))
        gta = {"when is part 2 coming out?": (["question", "asks_when", "asks_part2"], "r-part2"),
               "part 2 when?": (["question", "asks_when", "asks_part2"], "r-part2"),
               "when was this streamed?": (["question", "asks_when"], None),
               "when did he do this?": (["question", "asks_when"], None),
               "when does gta 6 come out?": (["question", "asks_when"], "r-gta6"),
               "who’s ready for gta 6?": (["question", "asks_who"], None),
               "that streamer is so annoying": ([], None),
               "whose stream is this?": (["question", "asks_who"], "r-stream"),
               "source?": (["question", "asks_source"], "r-stream")}
        for text, (tags, want) in gta.items():
            with self.subTest(text=text):
                self.assertEqual(self.starter_run(self.gta, text, tags), want)
        politics = {"who is paying for this?": (["question", "asks_who"], None),
                  "who voted for this guy?": (["question"], None),
                  "who are these people": (["question", "asks_who"], None),
                  "who is that?": (["question", "asks_who"], "r-who"),
                  "Who’s that?": (["question", "asks_who"], "r-who"),
                  "when will they ever learn?": (["question", "asks_when"], None),
                  "when is the next election?": (["question", "asks_when"], None),
                  "what hearing is this?": (["question", "asks_which"], "r-hearing"),
                  "when was this?": (["question", "asks_when"], "r-hearing"),
                  "source?": (["question", "asks_source"], "r-src")}
        for text, (tags, want) in politics.items():
            with self.subTest(text=text):
                self.assertEqual(self.starter_run(self.politics, text, tags), want)

    def test_an_anchored_rule_needs_both(self):
        r = {**base_rule(trigger={"intents": ["asks_when"], "keywords": ["gta 6"], "match": "all"}), "id": "x"}
        self.assertIsNone(RU.match(r, {"text": "when is it", "at": NOW.isoformat()}, ["asks_when"], NOW))
        self.assertIsNone(RU.match(r, {"text": "gta 6 hype", "at": NOW.isoformat()}, [], NOW))
        self.assertTrue(RU.match(r, {"text": "when is gta 6", "at": NOW.isoformat()}, ["asks_when"], NOW))
        with self.assertRaises(SystemExit) as e:
            RU.save(self.cfg, self.gta, base_rule(trigger={"intents": [], "keywords": ["x1"], "match": "all"}),
                    "Andrew", page=True)
        self.assertIn("needs both", str(e.exception))
        self.assertNotIn("match", RU.clean(base_rule())["trigger"])      # unanchored rules keep their old hash

    def test_part2_starter_promises_nothing(self):
        for v in next(r for r in RU.starters(self.gta) if r["id"] == "r-part2")["action"]["public"]["variants"]:
            self.assertNotRegex(v, r"is coming|on the way")

    def test_part2_without_a_source_is_skipped(self):
        self.practice(self.gta)
        RU.seed(self.cfg, self.gta, "Andrew", now=NOW - timedelta(days=3))
        it = self.add(self.gta, self.gitem("part 2 please", tags=["asks_part2"],
                                           post={"platform_id": "nope", "outlet": "instagram_reels"}))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        run = self.runs(self.gta)[0]
        self.assertEqual((run["result"], run["why"]), ("skipped", "no_fact"))
        self.assertEqual(self.queue(self.gta), [])

    # 19. the sign confirm reads as English
    def test_sign_words(self):
        r = RU.starters(self.politics)[0]
        w = RU.sign_words(self.politics, r)
        self.assertIn("when the comment:", w)
        self.assertIn("asks where it is from; or asks for the full video", w)
        self.assertIn("and uses one of the words:", w)
        self.assertNotIn("they asks", w.replace("they:", ""))

    def test_replay_counts_its_own_would_sends_against_the_limits(self):
        self.save(self.gta, base_rule(limits={"per_hour": 1, "per_day": 1, "person_days": 7}))
        for i in range(3):
            t = NOW - timedelta(hours=3 - i)
            self.add(self.gta, self.gitem("source?", who=f"5{i}", name=f"n{i}", tags=["asks_source"], at=t), now=t)
        got = RU.replay(self.cfg, self.gta, 7, rid=RU.rules(self.gta)[0]["id"], now=NOW)
        self.assertEqual(got["tally"]["would_send"], 1)
        self.assertEqual(sum(got["tally"]["why"].values()), 2)
        self.assertTrue(set(got["tally"]["why"]) <= {"rule_hour", "rule_day"})


class ForgetHolds(Base):
    """Review of 2026-10-20 (socialstore): a forget keeps the person's STOP and leaves no @name in the runs."""

    def test_a_stop_outlives_a_forget(self):
        self.signed_live(self.gta)
        self.grant(self.gta)
        self.warm(self.gta)
        SS.stop_contact(self.gta, "instagram:111", NOW - timedelta(days=1), why=SS.STOP_WHY)
        first = self.add(self.gta, self.gitem("source?"))[0]
        RU.on_items(self.cfg, self.gta, [first], NOW)
        SS.forget(self.gta, "instagram:111", "Andrew", cfg=self.cfg)
        self.assertTrue(SS.stopped(self.gta, "instagram:111"))
        self.assertFalse(SS.stopped(self.gta, "instagram:222"))
        raw = (SS.root(self.gta) / "forgotten.json").read_text()
        self.assertNotIn("instagram:", raw)                                  # the STOP is kept as a sha256 only
        again = self.add(self.gta, self.gitem("what is the source", at=NOW - timedelta(minutes=1)))[0]
        RU.on_items(self.cfg, self.gta, [again], NOW)
        self.assertEqual([(r["result"], r["why"]) for r in self.runs(self.gta)], [("skipped", "stop")] * 2)
        self.assertEqual(self.queue(self.gta), [])
        SS.stop_contact(self.gta, "instagram:111", NOW, on=False)           # taken back by hand: it goes
        self.assertFalse(SS.stopped(self.gta, "instagram:111"))

    def test_forget_without_a_stop_keeps_none(self):
        SS.touch_contact(self.gta, "instagram:111", NOW, name="sam", inbound=True)
        SS.forget(self.gta, "instagram:111", "Andrew", cfg=self.cfg)
        self.assertFalse(SS.stopped(self.gta, "instagram:111"))

    def test_forget_blanks_a_youtube_suggestion_naming_them(self):
        self.practice(self.gta)
        self.save(self.gta, base_rule(name="Yt", platforms=["youtube"], trigger={"keywords": ["credit"], "intents": []},
                                      action={"public": {"on": True, "variants": [
                                          "{mention} credit to {host}.", "{mention} by {host}, {source_title}.",
                                          "{mention} the video is {source_title}."]}}))
        it = self.add(self.gta, self.gitem("credit?", platform="youtube", name="Pat Smith",
                                           post=self.w.gta_post("youtube_shorts", pid="yt1")))[0]
        RU.on_items(self.cfg, self.gta, [it], NOW)
        self.assertIn("Pat Smith", json.dumps(self.runs(self.gta)))
        SS.forget(self.gta, "youtube:111", "Andrew", cfg=self.cfg)
        runs = "".join(f.read_text() for f in SS.root(self.gta).glob("runs-*.jsonl"))
        self.assertNotIn("Pat Smith", runs)
        self.assertEqual(self.runs(self.gta)[0]["suggested"], "[forgotten]")
        self.assertEqual(self.runs(self.gta)[0]["why"], "youtube")                # what happened stays


class Doctor(Base):
    def test_lines(self):
        rows = RU.doctor(self.cfg, NOW)
        self.assertTrue(any(r[0] is None and "Politics automations: off, no rules yet" in r[1] for r in rows))
        self.set(self.politics, {"automations": "practice"})
        RU.seed(self.cfg, self.politics, "Andrew", now=NOW - timedelta(days=2))
        it = self.add(self.politics, self.w.item(self.politics, self.w.politics_post(), "where is this from",
                                               tags=["asks_source"]))[0]
        RU.on_items(self.cfg, self.politics, [it], NOW)
        rows = RU.doctor(self.cfg, NOW)
        self.assertIn((None, "Politics automations: practice, 0 rules signed, 1 would-send in 7 days", ""),
                      rows)
        with SS.edit(self.politics, "rules") as d:
            d["rules"]["r-src"]["signoff"] = {"by": "Andrew", "at": NOW.isoformat(), "hash": "0" * 64}
        rows = RU.doctor(self.cfg, NOW)
        self.assertTrue(any(r[0] is False and "rule r-src changed after signing: sign it again (Inbox, Automations)"
                            in r[1] for r in rows))
        RU.save(self.cfg, self.politics, {**base_rule(trigger={"intents": ["asks_source"]}, action={
            "private": {"on": True, "variants": ["The full {kind}: {source_url}"]}}), "id": "r-src"}, "Andrew",
            page=True, now=NOW)
        rows = RU.doctor(self.cfg, NOW)
        self.assertIn((None, "private replies: wait for Meta's review; only app testers get them", ""), rows)

    def test_never_raises(self):
        store.write(store.path(self.gta.slug, "engage", "rules.json"), {"rules": "broken"})
        rows = RU.doctor(self.cfg, NOW)
        self.assertTrue(rows)

    def test_cli(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            RU.cli(self.cfg, ["seed", "gaming"])
            RU.cli(self.cfg, ["list", "gaming"])
            RU.cli(self.cfg, ["try", "gaming", "whose stream is this?"])
            RU.cli(self.cfg, ["replay", "gaming"])
        text = out.getvalue()
        self.assertIn("3 starter rules", text)
        self.assertIn("r-stream", text)
        with self.assertRaises(SystemExit) as e:
            RU.cli(self.cfg, ["sign", "gaming", "r-stream"])
        self.assertIn("review page", str(e.exception))
        reg = REG.COMMANDS.get("rules")
        self.assertEqual(reg, "rules")


class EngageIsolation(unittest.TestCase):
    """10.20 review: the tests that fake clipfactory.engage leave the real module where they found it."""

    def run_quietly(self, *names: str) -> None:
        import io
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n, sys.modules[__name__])
                                   for n in names)
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)

    def test_the_real_engage_is_put_back(self):
        import importlib
        real = importlib.import_module("clipfactory.engage")
        from clipfactory import engagepage as EP
        self.run_quietly("People.test_live_goes_through_the_fake_executor",
                         "Review1020.test_live_still_goes_when_nothing_changed")
        self.assertIs(sys.modules.get("clipfactory.engage"), real)
        REG.reset()
        self.assertIs(REG.load("engage"), real)                  # one copy, not a fresh import beside the old
        self.assertIs(EP.E, real)

    def test_an_absent_engage_stays_absent(self):
        key = "clipfactory.engage"
        saved = sys.modules.pop(key, None)
        self.addCleanup(REG.reset)
        if saved is not None:
            self.addCleanup(sys.modules.__setitem__, key, saved)
        self.run_quietly("People.test_live_goes_through_the_fake_executor")
        self.assertNotIn(key, sys.modules)


if __name__ == "__main__":
    unittest.main()
