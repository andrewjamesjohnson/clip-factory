import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Clip Review's Reports tab (clipfactory/reportspage.py, 2026-10-20): every view draws from files with no platform
# call, the tab sits in the header once the learner is built, each click (approve, decline, revert, start and stop
# an experiment) is refused unless it comes from the page itself, and its confirm names the exact words and where
# they go. The review page is served on a free port; the platforms are fakes that fail any call.

import json
import re
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from social_fakes import FakeGraph, FakeYouTube, cfg_in_tmp, patch_http, serve     # noqa: E402

from clipfactory import learn as L           # noqa: E402
from clipfactory import playbook as PB       # noqa: E402
from clipfactory import reportspage as RP    # noqa: E402
from clipfactory import store                # noqa: E402

D = date(2031, 7, 14)


def outside_code(html: str) -> str:
    return re.sub(r"<script>.*?</script>", "", re.sub(r"<style>.*?</style>", "", html, flags=re.S), flags=re.S)


class ReportsPageTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.g, self.yt = FakeGraph(), FakeYouTube()
        patch_http(self, graph=self.g, yt=self.yt)
        self.now = datetime(2031, 7, 15, 5, 0, tzinfo=self.cfg.tz())
        slots = {}
        for k in ("07:00", "10:30"):
            pa = self.cfg.slot_time(D, k)
            went = pa.astimezone(timezone.utc).isoformat()
            slots[k] = {"slot": k, "publish_at": pa.isoformat(), "status": "scheduled", "format": "underlay",
                        "slug": f"clip-{k[:2]}", "host": "MrBossFTW", "notes": [],
                        "outlets": {"facebook_reels": {"post_id": f"fb{k[:2]}", "platform_id": f"fb{k[:2]}",
                                                       "published_at": went, "provider": "direct",
                                                       "metrics": {"views": 300, "at": went}}}}
        slots["14:00"] = {"slot": "14:00", "publish_at": self.cfg.slot_time(D, "14:00").isoformat(),
                          "status": "failed", "notes": ["2031-07-14T00:00:00+00:00 candidate 1 failed"]}
        store.write(store.path("gaming", f"{D}.json"), {"account": "gaming", "date": str(D), "slots": slots})
        L.report(self.cfg, self.gta, day=D, now=self.now)
        with store.edit(L.proposals_path(self.gta), dict) as props:
            self.pid = L._put(self.gta, {"kind": "coach", "key": "coach.length_best.facebook_reels", "from": None,
                                         "to": [30, 45], "words": "The coach's best length on Facebook becomes 30 to "
                                                                  "45 seconds", "evidence": {"n": 24}}, props,
                              datetime(2031, 7, 15, tzinfo=timezone.utc))
        self.get, self.post = serve(self, self.cfg)

    def json_get(self, path):
        code, _, body = self.get(path)
        self.assertEqual(code, 200, body[:300])
        return json.loads(body)

    def test_every_view_draws_with_no_platform_call(self):
        code, headers, body = self.get("/reports")
        self.assertEqual(code, 200)
        self.assertIn("Content-Security-Policy", headers)
        html = body.decode()
        self.assertIn('id="rp-main"', html)
        self.assertIn("Biggest problems", html)
        self.assertIn("1 slot failed to make", html)
        for view in ("today", "week", "proposals", "experiments", "playbook"):
            code, _, body = self.get(f"/reports?view={view}&acct=gaming")
            self.assertEqual(code, 200, view)
            page = body.decode()
            self.assertNotIn("Could not draw", page, view)
            words = outside_code(page).replace("<!doctype", "")
            for bad in ("\u2014", "\u2013", "!"):
                self.assertNotIn(bad, words, f"{view}: {bad}")
            d = self.json_get(f"/reports/data?view={view}&acct=gaming")
            self.assertEqual(d["view"], view)
            self.assertTrue(d["stamp"])
            self.assertIn("rp-acct", d["html"])
        self.assertEqual((self.g.calls, self.yt.calls), ([], []))

    def test_the_tab_and_its_views(self):
        code, _, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn('href="/reports"', body.decode())
        page = self.get("/reports?view=proposals")[2].decode()
        self.assertIn('<a href="/reports" aria-current=page>Reports</a>', page)
        for label in ("Today", "Week", "Proposals", "Experiments", "Playbook"):
            self.assertIn(f">{label}</a>", page)
        self.assertIn("@media (max-width:600px)", page)
        self.assertIn("height:40px", page)

    def test_the_confirm_names_the_words_and_where_they_go(self):
        page = self.get("/reports?view=proposals&acct=gaming")[2].decode()
        m = re.search(r'data-op="learn-approve"[^>]*data-ask="([^"]+)"', page)
        self.assertTrue(m)
        ask = m.group(1).replace("&quot;", '"').replace("&#x27;", "'")
        self.assertIn('Approve for Gaming: "The coach', ask)
        self.assertIn("Gaming's playbook as version 1", ask)

    def test_clicks_need_the_page(self):
        for body in ({"op": "learn-approve", "account": "gaming", "id": self.pid},
                     {"op": "learn-decline", "account": "gaming", "id": self.pid, "reason": "other"},
                     {"op": "learn-revert", "account": "gaming", "version": 0},
                     {"op": "learn-exp-sign", "account": "gaming", "id": "e-0001"},
                     {"op": "learn-exp-stop", "account": "gaming", "id": "e-0001"}):
            code, d = self.post(body, page=False)
            self.assertEqual(code, 400, body)
            self.assertIn("Only a click on the review page", d.get("message", ""))
        self.assertEqual(PB.read(self.gta)["version"], 0)
        self.assertEqual(L.proposals(self.gta)[self.pid]["state"], "open")
        with self.assertRaises(SystemExit):
            RP.act(self.cfg, {"op": "learn-approve", "account": "gaming", "id": self.pid}, page=False)

    def test_approve_revert_decline_and_experiments_from_the_page(self):
        p = L.proposals(self.gta)[self.pid]
        code, d = self.post({"op": "learn-approve", "account": "gaming", "id": self.pid, "seen": "stale"})
        self.assertEqual(code, 400)
        self.assertIn("changed since the page showed it", d["message"])
        code, d = self.post({"op": "learn-approve", "account": "gaming", "id": self.pid, "seen": RP.seen(p)})
        self.assertEqual(code, 200, d)
        self.assertIn("playbook version 1", d["message"])
        self.assertEqual(PB.read(self.gta)["coach"]["length_best"], {"facebook_reels": [30.0, 45.0]})
        code, d = self.post({"op": "learn-revert", "account": "gaming", "version": 0})
        self.assertEqual(code, 200, d)
        self.assertEqual((PB.read(self.gta)["version"], PB.read(self.gta)["rules"]), (2, []))
        with store.edit(L.proposals_path(self.gta), dict) as props:
            p1 = L._put(self.gta, {"kind": "advice", "key": "advice.hour.x", "words": "More posts at 07:00",
                                   "evidence": {"n": 20}}, props, datetime(2031, 7, 15, tzinfo=timezone.utc))
            p2 = L._put(self.gta, {"kind": "advice", "key": "advice.first_comment.true.facebook_reels",
                                   "words": "More a first comment on Facebook",
                                   "evidence": {"n": 20, "outlet": "facebook_reels"}}, props,
                        datetime(2031, 7, 15, tzinfo=timezone.utc))
        code, d = self.post({"op": "learn-decline", "account": "gaming", "id": p2, "reason": "maybe"})
        self.assertEqual(code, 400)
        code, d = self.post({"op": "learn-decline", "account": "gaming", "id": p1, "reason": "test_first"})
        self.assertEqual(code, 400)                     # posting hours cannot be split clip by clip
        self.assertIn("cannot be split clip by clip", d["message"])
        self.assertEqual(L.proposals(self.gta)[p1]["state"], "open")
        code, d = self.post({"op": "learn-decline", "account": "gaming", "id": p2, "reason": "test_first",
                             "note": "try it on Facebook"})
        self.assertEqual(code, 200, d)
        self.assertEqual(L.proposals(self.gta)[p2]["decided"]["why"], "try it on Facebook")
        eid = sorted(L.experiments(self.gta))[0]
        self.assertEqual(self.post({"op": "learn-exp-sign", "account": "gaming", "id": eid})[0], 200)
        self.assertEqual(L.experiments(self.gta)[eid]["state"], "running")
        page = self.get("/reports?view=experiments&acct=gaming")[2].decode()
        self.assertIn("Stop it", page)
        self.assertEqual(self.post({"op": "learn-exp-stop", "account": "gaming", "id": eid})[0], 200)
        self.assertEqual(L.experiments(self.gta)[eid]["state"], "stopped")
        code, d = self.post({"op": "learn-approve", "account": "nobody", "id": self.pid})
        self.assertEqual(code, 400)
        self.assertEqual((self.g.calls, self.yt.calls), ([], []))

    def test_an_account_with_no_report(self):
        html = RP.render(self.cfg, "today", "politics", self.now)
        self.assertIn("No report yet", html)
        self.assertIn("factory.py learn daily --account politics", html)
        self.assertIn("No account to show here", RP.render(self.cfg, "today", "not-an-account", self.now))


    # ------------------------------------------------------------------------------------------- review fixes (10.20)
    def ask_of(self, html: str, op: str, has: str = "") -> str:
        """The confirm of the first `op` button whose words hold `has`."""
        for m in re.finditer(r'data-op="%s"[^>]*data-ask="([^"]+)"' % op, html):
            ask = m.group(1).replace("&quot;", '"').replace("&#x27;", "'")
            if has in ask:
                return ask
        self.fail(f"no {op} confirm with {has!r}")

    def test_the_today_subtitle_and_live_counts(self):
        html = RP.render(self.cfg, "today", "gaming", self.now)
        self.assertIn("report for Mon 07.14, the 04:40 report is off; this one was made by hand at 05:00", html)
        with store.edit(store.path("gaming", "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})["learn"] = "on"
        html = RP.render(self.cfg, "today", "gaming", self.now)
        self.assertIn("report for Mon 07.14, made at 05:00<", html)
        self.assertNotIn("04:40 at", html)
        # the proposal put after the report counts at once (the report itself said 0)
        self.assertEqual(L.report(self.cfg, self.gta, day=D, now=self.now, write=False)["kpis"].get("open_proposals"),
                         None)
        self.assertIn('<div class="k">Open proposals</div><div class="v">1</div>', html)

    def test_the_confirms_name_what_changes(self):
        t = datetime(2031, 7, 15, tzinfo=timezone.utc)
        PB.apply(self.cfg, self.gta, {"id": "p1", "key": "scout.channels.mrbossftw", "to": 1.3, "name": "MrBossFTW"},
                 "Andrew", t)
        PB.apply(self.cfg, self.gta, {"id": "p2", "key": "coach.first_word_max_s", "to": 0.5}, "Andrew", t)
        rid = PB.read(self.gta)["rules"][0]["id"]
        with store.edit(L.proposals_path(self.gta), dict) as props:
            L._put(self.gta, {"kind": "revert", "key": f"revert.{rid}", "from": 1,
                              "to": {"rule": rid, "key": "scout.channels.mrbossftw"},
                              "words": "Take back \"the scout ranks MrBossFTW's videos 1.3x\"", "evidence": {"n": 4}},
                   props, t)
        html = RP.render(self.cfg, "proposals", "gaming", self.now)
        ask = self.ask_of(html, "learn-approve", 'Approve for Gaming: "Take')
        self.assertIn("Takes out: the scout ranks MrBossFTW's videos 1.3x. Keeps: the coach wants the first word "
                      "within 0.5 seconds.", ask)
        self.assertIn('data-testable="0"', html)             # a coach length cannot be tested clip by clip
        hist = RP.render(self.cfg, "playbook", "gaming", self.now)
        self.assertIn("Version 2: the coach wants the first word within 0.5 seconds", hist)
        self.assertIn("Version 1: the scout ranks MrBossFTW&#x27;s videos 1.3x", hist)
        self.assertIn("Approved, waiting for the playbook switch", hist)
        back = self.ask_of(hist, "learn-revert")
        self.assertIn("Make Gaming's playbook version 1 again? It becomes version 3. Takes out: the coach wants "
                      "the first word within 0.5 seconds. Brings back: nothing. Keeps: the scout ranks MrBossFTW's "
                      "videos 1.3x. Then nothing reads it while the playbook switch is off.", back)

    def test_start_it_says_what_the_switch_allows(self):
        L.new_experiment(self.cfg, self.gta, question="Trial reels first?", kind="trial_reel", by="learner")
        html = RP.render(self.cfg, "experiments", "gaming", self.now)
        ask = self.ask_of(html, "learn-exp-sign")
        self.assertIn("written down only. The experiments switch is off: no clip gets an arm until it is on.", ask)
        with store.edit(store.path("gaming", "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})["learn.experiments"] = "on"
        ask = self.ask_of(RP.render(self.cfg, "experiments", "gaming", self.now), "learn-exp-sign",
                          )
        self.assertIn("It waits for the trial reels switch", ask)
        with store.edit(store.path("gaming", "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})["trial_reels"] = "on"
        ask = self.ask_of(RP.render(self.cfg, "experiments", "gaming", self.now), "learn-exp-sign",
                          )
        self.assertTrue(ask.endswith("signed with each clip."), ask)

    def test_a_draft_automation_confirm_says_it_is_noted_only(self):
        with store.edit(L.proposals_path(self.gta), dict) as props:
            L._put(self.gta, {"kind": "draft_rule", "key": "draft_rule.asks_source", "from": None,
                              "to": {"intent": "asks_source", "n": 9, "words": "where is this from"},
                              "words": "Draft an automation for where is this from",
                              "evidence": {"metric": "unanswered", "n": 9}}, props,
                   datetime(2031, 7, 15, tzinfo=timezone.utc))
        real = L.REG.fn
        with mock.patch.object(L.REG, "fn", lambda spec: None if spec == L.DRAFT_FN else real(spec)):  # not built
            html = RP.render(self.cfg, "proposals", "gaming", self.now)
        ask = self.ask_of(html, "learn-approve", 'Approve for Gaming: "Draft')
        self.assertIn("it is noted only: nothing is drafted or sent by this; make the rule on Inbox, Automations", ask)
        self.assertNotIn("read by the Automations page", html)
        html = RP.render(self.cfg, "proposals", "gaming", self.now)        # the automations part built
        ask = self.ask_of(html, "learn-approve", 'Approve for Gaming: "Draft')
        self.assertIn("the Automations page gets it as an unsigned draft in practice", ask)

    def test_title_links_are_40px_on_phones(self):
        self.assertIn("a.rp-ttl{display:flex;align-items:center;min-height:40px}}", RP.CSS)


if __name__ == "__main__":
    unittest.main()
