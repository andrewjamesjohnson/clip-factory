import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# News posts (2026-10-22): the Politics's picture posts on the day's news, a kind of card. The news desk lines a request
# up (factory.py cards news add); the request is checked against Andrew's rules (no hyphen or dash anywhere a person
# reads, no question for a headline, no party label on the picture, two pages the facts were checked on, every source
# shown as its own logo, a person or an agency from the atlas for the picture); five a day at the most; drawn at once
# or by the studio tick; signed on the Cards tab like every card; queued and sent with its own words per platform
# (the Facebook post's message, the Instagram carousel's caption); practice sends nothing. The real engine draws both
# slides. Throwaway folders, a fake atlas and fake logos, fake platforms, no network.

import json
import re
import shutil
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import FakeEngine, FakeGraph, patch_http   # noqa: E402
from test_cards import DAY, ENGINE_PY, IG, PAGE, Base, _engine_has_pil   # noqa: E402

from clipfactory import cards as CD             # noqa: E402
from clipfactory import cardspage as CP         # noqa: E402
from clipfactory import outward as OW           # noqa: E402

FB_WORDS = "The NSA says organizations that cannot update PHP right away should take those systems offline."
IG_WORDS = "Unpatched PHP servers should go offline, the NSA says.\n\n#cybersecurity #nsa #php"


def request(**over):
    req = {"id": "php-flaw", "at": "10:30", "kicker": "Cybersecurity",
           "headline": "Unpatched PHP servers should go offline, NSA says",
           "line": "Governments in Europe pulled websites this week over two critical flaws in PHP.",
           "hero": {"kind": "figure", "figure": "70%", "label": "of websites worldwide run on PHP, Wiz Research estimates."},
           "story": "PHP shipped a fix on Wednesday for two critical flaws. Amazon said attackers started probing them "
                    "four days earlier.",
           "sources": ["CNBC", "Newsweek"], "picture": "a-dept",
           "checked": ["https://www.cnbc.com/a", "https://www.newsweek.com/b"],
           "caption": {"facebook": FB_WORDS, "instagram": IG_WORDS}}
    req.update(over)
    return req


class NewsBase(Base):
    def setUp(self):
        super().setUp()
        atlas = self.politics.skill_dir / "broll-library" / "atlas"
        (atlas / "img").mkdir(parents=True, exist_ok=True)
        ents = {"a-dept": {"id": "a-dept", "kind": "agency", "name": "Department of Testing",
                           "title": "UNITED STATES GOVERNMENT", "licence": "Public domain", "licence_class": "pd",
                           "credit": "U.S. government"},
                "p-ross": {"id": "p-ross", "kind": "person", "name": "Pat Ross", "title": "CEO, ACME",
                           "eyebrow": "BUSINESS", "licence": "Public domain", "licence_class": "pd",
                           "credit": "The White House"},
                "p-cc": {"id": "p-cc", "kind": "person", "name": "Sam Lee", "title": "SENATOR",
                         "licence": "CC BY-SA 4.0", "licence_class": "cc", "credit": "Jane Doe"},
                "c-acme": {"id": "c-acme", "kind": "company", "name": "Acme", "licence": "Public domain"},
                "p-nopic": {"id": "p-nopic", "kind": "person", "name": "No Picture", "licence": "Public domain"}}
        (atlas / "registry.json").write_text(json.dumps({"entities": ents}))
        for f in ("a-dept.png", "p-ross.jpg", "p-cc.jpg", "c-acme.png"):
            (atlas / "img" / f).write_bytes(b"\x89PNG placeholder")
        self.logos = self.tmp / "logos"
        self.logos.mkdir(parents=True, exist_ok=True)
        for f in ("cnbc.png", "newsweek.png"):
            (self.logos / f).write_bytes(b"\x89PNG placeholder")
        (self.logos / "logos.json").write_text(json.dumps({
            "_how": "test", "CNBC": {"file": "cnbc.png", "how": "recolour", "scale": 1.25},
            "Newsweek": {"file": "newsweek.png"}, "Testing Dept": {"atlas": "a-dept", "how": "keep"},
            "Gone": {"file": "gone.png"}}))
        p = mock.patch.object(CD, "LOGOS_DIR", self.logos)
        p.start()
        self.addCleanup(p.stop)
        CD._ATLAS_CACHE.clear()
        self.set(self.politics, {"cards.make": "on", "cards.post": "practice"},
                 {"cards.kinds": ["news"], "cards.targets": ["facebook_feed", "instagram_carousel"]})

    def add(self, req=None, hhmm="09:00"):
        return CD.add_news(self.cfg, self.politics, req or request(), "news desk", self.at(hhmm))

    def card(self, cid="news-php-flaw"):
        return CD.get_card(self.politics, DAY, cid)


# ---------------------------------------------------------------------------------------------- the request
class NewsRequestTest(NewsBase):
    def test_1_a_good_request_is_lined_up_and_drawn_at_once(self):
        lines = self.add()
        self.assertTrue((CD.news_dir(self.politics, DAY) / "php-flaw.json").is_file(), lines)
        c = self.card()
        self.assertIsNotNone(c, lines)
        self.assertEqual((c["kind"], c["status"]), ("news", "ready"))
        self.assertEqual(c["targets"], ["facebook_feed", "instagram_carousel"])
        self.assertEqual(c["at"], CD._iso(self.cfg.slot_time(DAY, "10:30")))
        self.assertEqual((len(c["files"]["images"]), len(c["files"]["videos"])), (2, 2))   # two slides, IG copies
        self.files_under_state(c)
        w = c["words"]
        self.assertEqual(w["dateline"], "Monday · November 2 2026")
        self.assertEqual(w["caption"], {"facebook": FB_WORDS, "instagram": IG_WORDS})
        self.assertEqual(w["picture"], {"name": "Department of Testing", "title": "UNITED STATES GOVERNMENT",
                                        "eyebrow": ""})
        self.assertEqual(c["source"]["checked"], ["https://www.cnbc.com/a", "https://www.newsweek.com/b"])
        spec = FakeEngine.calls(self.eng.parent)[-1]["spec"]
        self.assertEqual([s["layout"] for s in spec["slides"]], ["news", "news_more"])
        self.assertEqual(spec["slides"][0]["picture"]["kind"], "seal")
        self.assertEqual([x["name"] for x in spec["slides"][0]["sources"]], ["CNBC", "Newsweek"])
        self.assertEqual(spec["slides"][0]["sources"][0]["scale"], 1.25)
        self.assertEqual(spec["slides"][1]["hero"]["figure"], "70%")
        self.assertIsNone(CD.read_board(self.politics, DAY).get("made"))   # the day's own make still runs

    def test_2_each_rule_refuses_with_its_reason(self):
        cases = [
            (request(headline="A hard-line plan, NSA says"), "headline has a hyphen or a dash"),
            (request(story="PHP shipped a fix — late."), "story has a hyphen or a dash"),
            (request(headline="Will PHP servers go offline"[:80] + "?"), "headline is a question"),
            (request(line="Republicans and the NSA agree on PHP."), "line names a party"),
            (request(checked=["https://www.cnbc.com/a"]), "checked: the two or more pages"),
            (request(caption={"facebook": "See https://x.com/a", "instagram": IG_WORDS}), "caption.facebook has a link"),
            (request(caption={"facebook": FB_WORDS, "instagram": "#a #b #c #d #e #f"}), "more than 5 hashtags"),
            (request(caption={"facebook": "A well-known flaw.", "instagram": IG_WORDS}),
             "caption.facebook has a hyphen or a dash"),
            (request(sources=["Reuters"]), "no logo for 'Reuters'"),
            (request(sources=["Gone"]), "no logo for 'Gone'"),
            (request(picture="c-acme"), "a company card"),
            (request(picture="p-nopic"), "has no image"),
            (request(picture="p-nobody"), "is not in the atlas"),
            (request(id="PHP flaw"), "id: a few lowercase words"),
            (request(at="23:30"), "at: the time it goes"),
            (request(hero={"kind": "figure", "figure": "many", "label": "x"}), "hero.figure must be a number"),
            (request(hero={"kind": "quote", "quote": "Off the charts.", "name": "", "title": "CEO"}), "hero.name"),
            (request(headline="x" * 81), "headline is 81 characters"),
        ]
        for req, want in cases:
            bad, _ = CD.check_news(self.politics, req)
            self.assertTrue(any(want in b for b in bad), (want, bad))
            with self.assertRaises(SystemExit):
                self.add(req)
        self.assertEqual(CD.check_news(self.politics, request())[0], [])
        self.assertEqual(CD.check_news(self.politics, request(sources=["Testing Dept", "CNBC"]))[0], [])
        self.assertEqual(CD.check_news(self.politics, request(sources=["Department of Testing"]))[0], [])  # by atlas name
        self.assertFalse(CD.news_dir(self.politics, DAY).is_dir() and list(CD.news_dir(self.politics, DAY).glob("*")))

    def test_3_five_a_day_and_never_the_same_id_twice(self):
        self.add()
        with self.assertRaises(SystemExit) as e:
            self.add()
        self.assertIn("already lined up", str(e.exception))
        for i, at in enumerate(("11:00", "13:00", "15:30", "17:30")):     # five a day (Andrew 10.28: "3 to 5")
            self.add(request(id=f"more{i}", at=at))
        with self.assertRaises(SystemExit) as e:
            self.add(request(id="sixth", at="19:00"))
        self.assertIn("5 a day at the most", str(e.exception))

    def test_3b_show_then_sign_only_what_andrew_was_sent(self):
        # Andrew 10.28: the desk sends the finished post to his phone; his "post it" signs exactly those pictures
        self.add()
        shown = CD.show_news(self.cfg, self.politics, DAY, "php-flaw")
        self.assertIn("headline: Unpatched PHP servers should go offline, NSA says", shown)
        self.assertIn("slides: 2", shown)
        h = shown.split("hash: ")[1].strip()
        with self.assertRaises(SystemExit):                                 # no hash, or the desk's own name
            CD.cli(self.cfg, ["news", "sign", "politics", "php-flaw", "--date", str(DAY)])
        with self.assertRaises(SystemExit):
            CD.cli(self.cfg, ["news", "sign", "politics", "php-flaw", "--date", str(DAY), "--seen", h])
        with self.assertRaises(SystemExit) as e:                            # a hash from other pictures
            CD.approve(self.cfg, self.politics, DAY, "news-php-flaw", "0" * 64, "Andrew (phone): post it",
                       self.at("08:00"))
        self.assertIn("changed after the page showed it", str(e.exception))
        CD.approve(self.cfg, self.politics, DAY, "news-php-flaw", h, "Andrew (phone): post it", self.at("08:00"))
        c = self.card()
        self.assertEqual(c["status"], "approved")
        self.assertEqual(c["signoff"]["by"], "Andrew (phone): post it")
        with self.assertRaises(SystemExit):
            CD.show_news(self.cfg, self.politics, DAY, "nothing-here")

    def test_4_off_until_news_is_one_of_the_kinds(self):
        self.set(self.politics, settings={"cards.kinds": ["quote"]})
        with self.assertRaises(SystemExit) as e:
            self.add()
        self.assertIn("cards.kinds news", str(e.exception))

    def test_5_a_picture_that_asks_for_credit_gets_it_in_the_words(self):
        self.add(request(picture="p-cc"))
        cap = self.card()["words"]["caption"]
        self.assertTrue(cap["facebook"].endswith("Picture: Jane Doe, CC BY-SA 4.0"), cap)
        self.assertTrue(cap["instagram"].endswith("Picture: Jane Doe, CC BY-SA 4.0"), cap)
        self.add(request(id="ross", picture="p-ross", at="15:00"))
        c = self.card("news-ross")
        self.assertEqual(c["words"]["caption"]["facebook"], FB_WORDS)        # public domain: no credit line
        self.assertEqual(c["words"]["picture"]["eyebrow"], "BUSINESS")
        spec = FakeEngine.calls(self.eng.parent)[-1]["spec"]
        self.assertEqual(spec["slides"][0]["picture"]["kind"], "portrait")

    def test_6_after_hours_it_waits_for_the_tick_and_a_broken_one_is_said_once(self):
        self.add(hhmm="05:50")                                   # before 06:26: lined up, not drawn
        self.assertIsNone(self.card())
        folder = CD.news_dir(self.politics, DAY)
        bad = request(id="broken", headline="A hard-line plan")
        (folder / "broken.json").write_text(json.dumps(bad))       # written straight into the folder (not checked)
        lines = CD.tick(self.cfg, self.at("06:40"))
        self.assertEqual(self.card()["status"], "ready", lines)
        self.assertTrue(any("broken: not drawn" in ln for ln in lines), lines)
        self.assertIn("broken", CD.read_board(self.politics, DAY)["news_refused"])
        again = CD.tick(self.cfg, self.at("06:50"))
        self.assertFalse(any("broken" in ln for ln in again), again)
        self.assertIsNone(self.card("news-broken"))

    def test_7_make_again_keeps_its_id_and_draws_a_new_folder(self):
        self.add()
        old = Path(self.card()["files"]["images"][0]["path"])
        CD.remake(self.cfg, self.politics, DAY, "news-php-flaw", "Andrew", self.at("09:10"))
        new = Path(self.card()["files"]["images"][0]["path"])
        self.assertEqual((old.parent.name, new.parent.name), ("r1", "r2"))
        self.assertEqual(sorted(CD.read_board(self.politics, DAY)["cards"]), ["news-php-flaw"])

    def test_8_the_cards_page_shows_every_word_that_goes(self):
        self.add()
        html = CP._words_html(self.card())
        for want in ("Unpatched PHP servers should go offline, NSA says", "70%", "Sources: CNBC, Newsweek",
                     "Facebook words", "Instagram words", "#cybersecurity"):
            self.assertIn(want, html)
        self.assertIn("facts checked on 2 pages", CP._source_line(self.card()))


# ---------------------------------------------------------------------------------------------- posting
class NewsPostTest(NewsBase):
    def setUp(self):
        super().setUp()
        self.grant(self.politics)
        self.g = FakeGraph()
        patch_http(self, graph=self.g)
        self.add()
        c = self.card()
        CD.approve(self.cfg, self.politics, DAY, "news-php-flaw", CD.card_hash(c), "Andrew", self.at("09:05"))

    def test_1_practice_sends_nothing_and_logs_the_words(self):
        CD.tick(self.cfg, self.at("10:30"))
        CD.tick(self.cfg, self.at("10:31"))
        self.assertEqual(self.g.calls, [])
        c = self.card()
        self.assertEqual(c["status"], "posted")
        self.assertEqual({t: o["state"] for t, o in c["outlets"].items()},
                         {"facebook_feed": "practice", "instagram_carousel": "practice"})
        texts = {r.get("outlet"): r.get("text") for r in OW.rows(self.cfg, "politics",
                                                                  since=self.at("09:00") - timedelta(days=1))
                 if r.get("kind") == "card_post" and r.get("text")}
        self.assertEqual(texts.get("facebook_feed"), FB_WORDS)
        self.assertEqual(texts.get("instagram_carousel"), IG_WORDS)

    def test_2_on_it_goes_with_its_words(self):
        self.set(self.politics, {"cards.post": "on"})
        n = {"c": 0}

        def media(call):
            n["c"] += 1
            return {"id": f"c{n['c']}", "uri": f"https://rupload.facebook.com/ig-api-upload/v25.0/c{n['c']}"}
        self.g.on("POST", rf"/{PAGE}/photos$", lambda c: {"id": f"ph{len(self.g.calls)}"})
        self.g.on("POST", rf"/{PAGE}/feed$", {"id": f"{PAGE}_9"})
        self.g.on("GET", rf"/{IG}/content_publishing_limit$", {"data": [{"config": {"quota_total": 50},
                                                                          "quota_usage": 2}]})
        self.g.on("POST", rf"/{IG}/media$", media)
        self.g.on("POST", r"rupload\.facebook\.com/ig-api-upload/", {"success": True})
        self.g.on("GET", r"^/c\d+$", {"status_code": "FINISHED"})
        self.g.on("POST", rf"/{IG}/media_publish$", {"id": "m9"})
        self.g.on("GET", r"^/m9$", {"permalink": "https://www.instagram.com/p/m9/"})
        for hhmm in ("10:30", "10:40", "10:50"):                  # one card post a tick, one per platform
            CD.tick(self.cfg, self.at(hhmm))
        feed = next(c for c in self.g.calls if c["path"] == f"/{PAGE}/feed")
        self.assertEqual(feed["form"]["message"], FB_WORDS)
        made = [c["form"] for c in self.g.calls if c["path"] == f"/{IG}/media"]
        self.assertEqual(made[-1]["media_type"], "CAROUSEL")
        self.assertEqual(made[-1]["caption"], IG_WORDS)
        self.assertFalse(any("caption" in m for m in made[:-1]))   # the children carry none
        self.assertEqual(self.card()["status"], "posted")

    def test_3_the_other_cards_still_go_with_no_words(self):
        self.assertEqual(CD.news_caption({"kind": "docket", "words": {"caption": {"facebook": "x"}}}, "facebook_feed"), "")
        self.assertEqual(CD.news_caption(self.card(), "instagram_carousel"), IG_WORDS)


# ---------------------------------------------------------------------------------------------- the real engine
@unittest.skipUnless(_engine_has_pil(), "the engine Python (/usr/bin/python3) has no Pillow, fontTools or fonts here")
class NewsRealEngineTest(unittest.TestCase):
    """Both slides drawn by the real render/card_engine.py with the real atlas and logos: 1080x1350, the type inside
    x 72..1008 and y 80..1270 (the quote's opening mark may hang into the margin), the ground the blurred picture."""

    def test_1_both_slides_draw_inside_the_margins(self):
        vce = Path(os.path.expanduser("~/.claude/skills/viral-clip-engine"))
        img = vce / "broll-library" / "atlas" / "img"
        logos = Path(__file__).resolve().parent.parent / "render" / "logos"
        if not ((vce / "scripts" / "politicslook.py").is_file() and (img / "a-national-security-agency.png").is_file()
                and (img / "p-q305177.jpg").is_file()):
            self.skipTest("the skill's atlas is not on this Mac")
        engine = Path(__file__).resolve().parent.parent / "render" / "card_engine.py"
        tmp = Path(tempfile.mkdtemp(prefix="clipfactory-news-real-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        srcs = [{"name": "CNBC", "path": str(logos / "cnbc.png"), "how": "recolour", "scale": 1.25},
                {"name": "Newsweek", "path": str(logos / "newsweek.png"), "how": "recolour", "scale": 1.0},
                {"name": "The Hill", "path": str(logos / "thehill.png"), "how": "bone", "scale": 1.0}]
        seal = {"kind": "seal", "path": str(img / "a-national-security-agency.png"), "name": "National Security Agency",
                "title": "UNITED STATES GOVERNMENT", "eyebrow": ""}
        face = {"kind": "portrait", "path": str(img / "p-q305177.jpg"), "name": "Jensen Huang", "title": "CEO, NVIDIA",
                "eyebrow": "BUSINESS"}
        common = {"dateline": "Thursday · October 22 2026", "sources": srcs}
        slides = [dict(common, layout="news", picture=seal, kicker="Cybersecurity",
                       headline="Unpatched PHP servers should go offline, NSA says",
                       line="Governments in Europe pulled websites this week over two critical flaws in PHP."),
                  dict(common, layout="news_more", picture=seal, story="PHP shipped a fix on Wednesday. " * 4,
                       hero={"kind": "figure", "figure": "70%", "label": "of websites run on PHP."}),
                  dict(common, layout="news", picture=face, kicker="Markets",
                       headline="Jensen Huang says AI cloud demand is “off the charts”", line="He also told CNBC."),
                  dict(common, layout="news_more", picture=face, story="Neoclouds rent out computing power. " * 3,
                       hero={"kind": "quote", "quote": "The demand for the neoclouds is off the charts.",
                             "name": "Jensen Huang", "title": "CEO, NVIDIA"})]
        outs = [str(tmp / f"{i + 1}.jpg") for i in range(len(slides))]
        sp = tmp / "spec.json"
        sp.write_text(json.dumps({"v": 1, "look": "politics", "kind": "news", "skill_dir": str(vce), "kit": "gta",
                                  "size": [1080, 1350], "slides": slides, "outputs": outs}))
        r = subprocess.run([ENGINE_PY, "-B", str(engine), str(sp)], capture_output=True, text=True, timeout=120)
        got = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(got.get("ok"), (got, r.stderr[-400:]))
        measure = ("import json, sys\nfrom PIL import Image\nout = []\nfor p in sys.argv[1:]:\n"
                   "    im = Image.open(p).convert('L'); w, h = im.size; px = im.load()\n"
                   "    lit = lambda xs, ys: sum(1 for y in ys for x in xs if px[x, y] > 150)\n"
                   "    out.append([w, h, lit(range(0, 40), range(h)), lit(range(1012, w), range(h)),\n"
                   "                lit(range(w), list(range(0, 76)) + list(range(1274, h)))])\n"
                   "print(json.dumps(out))")
        r = subprocess.run([ENGINE_PY, "-c", measure] + outs, capture_output=True, text=True, timeout=60)
        for (w, h, left, right, edges), out in zip(json.loads(r.stdout), outs):
            self.assertEqual((w, h), (1080, 1350), out)
            self.assertEqual((left, right, edges), (0, 0, 0), out)
        dest = os.environ.get("CARDS_PREVIEW_DIR")
        if dest:
            for o in outs:
                Path(dest).mkdir(parents=True, exist_ok=True)
                shutil.copy(o, Path(dest) / f"news-{Path(o).name}")


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------------------------- the phone's buttons (2026-10-29)
class NewsPhoneTest(NewsBase):
    """`cards news ask` parks a checked request; the phone's Yes makes it, Post it signs exactly the pictures sent."""

    def ref(self, *more):
        return [CD._slug(self.politics), str(DAY), "php-flaw", *more]

    def test_yes_makes_it_and_post_it_signs_those_pictures(self):
        from clipfactory import newsphone as NP
        said = NP.ask(self.cfg, self.politics, request(), "The NSA says unpatched PHP servers should go offline",
                      self.at("09:00"))
        self.assertIn("PHONE: not sent (offline)", said[-1])
        self.assertIsNone(self.card())                               # nothing is made before his yes
        with self.assertRaises(SystemExit):
            NP.ask(self.cfg, self.politics, request(), "again", self.at("09:01"))
        NP.answer(self.cfg, ["yes", *self.ref()], self.at("09:10"))
        c = self.card()
        self.assertEqual(c["status"], "ready")
        self.assertEqual(NP._read(self.politics, DAY, "php-flaw")["status"], "made")
        self.assertEqual(NP.answer(self.cfg, ["yes", *self.ref()], self.at("09:11")), ["  news php-flaw: made already"])
        bad = NP.answer(self.cfg, ["post", *self.ref("0" * 64)], self.at("09:12"))
        self.assertIn("not signed", bad[0])
        self.assertEqual(self.card()["status"], "ready")
        NP.answer(self.cfg, ["post", *self.ref(CD.card_hash(c))], self.at("09:13"))
        c = self.card()
        self.assertEqual(c["status"], "approved")
        self.assertEqual(c["signoff"]["by"], "Andrew (phone): Post it (the button)")

    def test_no_and_too_late_make_nothing(self):
        from clipfactory import newsphone as NP
        NP.ask(self.cfg, self.politics, request(), "a line", self.at("09:00"))
        NP.answer(self.cfg, ["no", *self.ref()], self.at("09:05"))
        self.assertEqual(NP.answer(self.cfg, ["yes", *self.ref()], self.at("09:06")), ["  news php-flaw: no already"])
        self.assertIsNone(self.card())
        NP.ask(self.cfg, self.politics, request(id="late-one"), "a line", self.at("09:00"))
        out = NP.answer(self.cfg, ["yes", CD._slug(self.politics), str(DAY), "late-one"], self.at("12:30"))
        self.assertIn("after 3 hours", out[0])
        self.assertIsNone(self.card("news-late-one"))
        with self.assertRaises(SystemExit):                          # a request that breaks a rule is never asked
            NP.ask(self.cfg, self.politics, request(id="bad", headline="Is this a question?"), "x", self.at("09:00"))
