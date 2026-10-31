import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Clip Review, Inbox, Automations (rulespage.py), 2026-10-20: the page under the Inbox tab, its /data from files
# only, and its clicks. Every op needs a click on the page itself; Sign takes the hash the page showed; Try it and the
# 7-day replay answer as JSON and write nothing, with no network call. The look: native tokens, au- classes, phones
# from 280 px (40 px targets, 16 px inputs), plain words with no long dashes. Nothing reaches the network.

import json
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipfactory import registry as REG        # noqa: E402
from clipfactory import rules as RU            # noqa: E402
from clipfactory import rulespage as RP        # noqa: E402
from clipfactory import socialstore as SS      # noqa: E402
from clipfactory import store                  # noqa: E402
from clipfactory import switches as SW         # noqa: E402
from social_fakes import serve                 # noqa: E402
from test_answers import NOW, World, no_network   # noqa: E402

OPS = ("rule-save", "rule-sign", "rule-mode", "rule-dup", "rule-delete", "rule-try", "rule-replay", "rule-seed")


class Page(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        self.cfg = self.w.cfg
        no_network(self)
        self.get, self.post = serve(self, self.cfg)

    def snapshot(self):
        out = {}
        for p in sorted(Path(store.path()).rglob("*")):
            if p.is_file() and not p.name.endswith(".lock") and "logs" not in p.parts:
                out[str(p)] = p.read_bytes()
        return out

    def test_page_is_served_with_the_csp_under_the_inbox(self):
        code, h, body = self.get("/inbox/rules")
        self.assertEqual(code, 200)
        self.assertIn("img-src 'self'", h.get("Content-Security-Policy", ""))
        html = body.decode()
        self.assertIn("<title>Clip Review · Automations</title>", html)
        self.assertIn('class="sx sx-rules"', html)
        self.assertIn("/inbox/rules/data", html)
        code, _, _ = self.get("/inbox/rules", headers={"Host": "evil.example"})
        self.assertEqual(code, 403)

    def test_data_from_files_with_a_stamp(self):
        RU.seed(self.cfg, self.w.gta, "Andrew", now=NOW)
        code, _, body = self.get("/inbox/rules/data?acct=gaming")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertTrue(d["stamp"])
        acct = d["accounts"][0]
        self.assertEqual(acct["slug"], "gaming")
        self.assertEqual([r["id"] for r in acct["rules"]], ["r-stream", "r-gta6", "r-part2"])
        r = acct["rules"][0]
        self.assertEqual(r["signed"], "unsigned")
        self.assertEqual(r["hash"], RU.rule_hash(RU.get(self.w.gta, "r-stream")))
        self.assertIn("Sign \"Whose stream is this\" for Gaming?", r["sign_words"])
        self.assertTrue(acct["checklist"])

    def test_every_op_needs_the_page(self):
        for op in OPS:
            with self.subTest(op=op):
                code, d = self.post({"op": op, "account": "gaming", "id": "r-stream", "text": "source?"}, page=False)
                self.assertEqual(code, 400)
                self.assertIn("Only a click on the review page", d["message"])

    def test_seed_sign_mode_from_the_page(self):
        code, d = self.post({"op": "rule-seed", "account": "politics"})
        self.assertEqual(code, 200, d)
        self.assertIn("starter rules added", d["message"])
        r = RU.get(self.w.politics, "r-src")
        code, d = self.post({"op": "rule-sign", "account": "politics", "id": "r-src", "seen_hash": "old"})
        self.assertEqual(code, 400)
        self.assertIn("changed since the page showed it", d["message"])
        code, d = self.post({"op": "rule-sign", "account": "politics", "id": "r-src",
                             "seen_hash": RU.rule_hash(r)})
        self.assertEqual(code, 200, d)
        self.assertEqual(RU.signed_state(RU.get(self.w.politics, "r-src")), "signed")
        code, d = self.post({"op": "rule-mode", "account": "politics", "id": "r-src", "mode": "live"})
        self.assertEqual(code, 400)
        self.assertIn("stays practice", d["message"])
        code, d = self.post({"op": "rule-mode", "account": "politics", "id": "r-src", "mode": "paused"})
        self.assertEqual((code, RU.get(self.w.politics, "r-src")["mode"]), (200, "paused"))

    def test_save_and_duplicate_from_the_page(self):
        rule = {"name": "Credit", "platforms": ["instagram"],
                "trigger": {"on": "comment", "posts": "any", "intents": [], "keywords": "credit, who made",
                            "exclude": "", "max_age_h": 24},
                "action": {"public": {"on": True, "variants": ["{mention} credit to {host}.",
                                                               "{mention} made by {host}, {source_title}.",
                                                               "{mention} the video is {source_title}."]},
                           "private": {"on": False, "variants": []}, "part2": {"on": False}},
                "limits": {"per_hour": 3, "per_day": 10, "person_days": 7}}
        code, d = self.post({"op": "rule-save", "account": "gaming", "rule": rule})
        self.assertEqual(code, 200, d)
        got = RU.rules(self.w.gta)[0]
        self.assertEqual(got["trigger"]["keywords"], ["credit", "who made"])
        code, d = self.post({"op": "rule-save", "account": "gaming", "id": got["id"],
                             "rule": dict(rule, name="Credit two"), "seen_version": 7})
        self.assertEqual(code, 400)
        self.assertIn("changed since the page showed it", d["message"])
        code, d = self.post({"op": "rule-dup", "account": "gaming", "id": got["id"], "to": "politics"})
        self.assertEqual(code, 200, d)
        self.assertEqual(RU.rules(self.w.politics)[0]["mode"], "practice")
        code, d = self.post({"op": "rule-delete", "account": "gaming", "id": got["id"]})
        self.assertEqual(code, 200, d)
        self.assertEqual(RU.rules(self.w.gta), [])

    def test_try_and_replay_write_nothing_and_reach_no_network(self):
        real = datetime.now(timezone.utc)                  # the page's clicks run on the clock
        RU.seed(self.cfg, self.w.gta, "Andrew", now=real - timedelta(days=2))
        it = self.w.item(self.w.gta, self.w.gta_post(), "whose stream is this?", tags=["asks_who"],
                         at=real - timedelta(hours=3))
        SS.upsert_items(self.w.gta, [it], real)
        before = self.snapshot()
        code, d = self.post({"op": "rule-try", "account": "gaming", "text": "whose stream is this?",
                             "platform": "instagram"})
        self.assertEqual(code, 200, d)
        got = json.loads(d["message"])
        self.assertEqual(got["run"]["rule"], "r-stream")
        code, d = self.post({"op": "rule-replay", "account": "gaming", "id": "r-stream", "days": 7})
        self.assertEqual(code, 200, d)
        got = json.loads(d["message"])
        self.assertEqual((got["tally"]["comments"], got["tally"]["would_send"]), (1, 1))
        draft = dict(RU.get(self.w.gta, "r-stream"), name="Edited")
        code, d = self.post({"op": "rule-replay", "account": "gaming", "rule": draft})
        self.assertEqual(json.loads(d["message"])["tally"]["matched"], 1)
        self.assertEqual(before, self.snapshot())

    def test_unknown_account_refused(self):
        code, d = self.post({"op": "rule-seed", "account": "nobody"})
        self.assertEqual(code, 400)
        self.assertIn("not one of your accounts", d["message"])

    def test_off_page_draws_without_rules(self):
        d = RP.data(self.cfg, {}, NOW)
        self.assertTrue(all(a["mode"] == "off" for a in d["accounts"]))
        self.assertTrue(all(a["rules"] == [] for a in d["accounts"]))

    def test_phone_and_copy(self):
        css = RP.CSS
        self.assertIn("@media (max-width:600px)", css)
        self.assertIn("font-size:16px", css)
        self.assertIn("body.sx button.au-row{height:auto;min-height:40px", css)
        for bad in ("gradient", "glow", "box-shadow", "dotted"):
            self.assertNotIn(bad, css)
        self.assertNotRegex(css, r"(?<!body)\.(?!au-|sx-)[a-z]")
        for path in (RU.__file__, RP.__file__, ROOT / "clipfactory" / "answers.py"):
            text = Path(path).read_text()
            self.assertNotIn("\u2014", text, path)
            self.assertNotIn("\u2013", text, path)
        html = self.get("/inbox/rules")[2].decode()
        visible = re.sub(r"<script>.*?</script>|<style>.*?</style>", "", html, flags=re.S)
        self.assertNotIn("!", re.sub(r"<!doctype html>", "", visible, flags=re.I))

    # review 10.20 -----------------------------------------------------------------------------------------
    def test_the_stamp_holds_between_polls(self):
        RU.seed(self.cfg, self.w.gta, "Andrew", now=NOW - timedelta(days=2))
        a = RP.data(self.cfg, {}, NOW)
        b = RP.data(self.cfg, {}, NOW + timedelta(seconds=15))
        self.assertEqual(a["stamp"], b["stamp"])          # the editor and Try it are not redrawn every poll
        self.assertNotEqual(a["now"], b["now"])
        self.assertIsInstance(a["accounts"][0]["rules"][0]["practiced_h"], int)
        self.assertEqual(a["skip_words"]["person_7d"], RU.SKIP_WORDS["person_7d"])

    def test_try_it_and_folds_survive_a_redraw(self):
        js = RP.JS
        self.assertIn("tryText.value = S.T.text", js)
        self.assertIn("!S.dirty && !busy()", js)
        self.assertIn("S.folds", js)
        self.assertNotIn("el('details', {}", js)

    def test_testers_and_live_confirms_show_every_word(self):
        js = RP.JS
        i = js.index("m === 'testers' ?")
        self.assertIn("sayWords()", js[i:i + 400])
        self.assertIn("part2_words", js)
        d = RP.data(self.cfg, {}, NOW)
        self.assertEqual(d["part2_words"], [list(RU.PART2_PRIVATE), list(RU.PART2_PUBLIC)])

    def test_the_numbers_for_24_hours_and_all_time(self):
        js = RP.JS
        self.assertIn("statBox('Last 24 hours'", js)
        self.assertIn("statBox('All the time kept'", js)
        self.assertIn("String(s.failed || 0)", js)
        self.assertIn("skipWord(k)", js)

    def test_subnav_matches_the_inbox(self):
        from unittest import mock
        real = REG.present
        with mock.patch.object(REG, "present", side_effect=lambda name: name == "engagepage" or real(name)):
            html = RP.page(self.cfg, "tok", "", "", "", "all", {})
        nav = re.search(r'<nav class="sx-subnav".*?</nav>', html, flags=re.S).group(0)
        self.assertEqual(re.findall(r">([^<]+)</a>", nav), ["Comments", "Messages", "Saved replies", "Automations"])

    def test_long_names_and_narrow_desktops(self):
        css = RP.CSS
        self.assertIn(".au-run-h b{min-width:0;overflow-wrap:anywhere}", css)
        self.assertIn("@media (max-width:1000px){.au-row.au-th{display:none}", css)

    def test_phone_folds_checkboxes_and_back_are_40px(self):
        # review 10.20: on phones the folds were 18 px, the action checkboxes 28 px, the Back label stuck to the top
        css = RP.CSS
        phone = css[css.index("@media (max-width:600px)"):]
        self.assertIn(".au-acct details summary,#au details>summary{min-height:40px;display:flex;align-items:center}",
                      phone)
        self.assertIn(".au-chk{min-height:40px}", phone)
        self.assertIn(".au-chk input{width:22px;height:22px;flex:none}", phone)
        self.assertIn(".au-back{display:inline-flex;align-items:center;justify-content:center}", phone)
        self.assertIn("const main = document.getElementById('au')", RP.JS)   # #au is the page the folds live in

    def test_registry_names_it(self):
        self.assertEqual(REG.route("/inbox/rules")[0], "page")
        self.assertEqual(REG.route("/inbox/rules/data")[0], "data")
        self.assertIs(REG.act_module("rule-try"), RP)
        self.assertEqual(REG.top_of("rules"), "inbox")
        self.assertIn("rules", REG.DOCTORS)


if __name__ == "__main__":
    unittest.main()
