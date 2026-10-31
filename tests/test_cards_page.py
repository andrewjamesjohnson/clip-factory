import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Clip Review's Cards tab (2026-10-20): the page and its /data, served for real on a free port; previews only through
# /file from state/; every click that acts needs the page itself and confirms the exact words and where they go;
# the phone rules (one column, 40px targets, 16px inputs, a story at most 58vh, a carousel that scrolls). Throwaway
# folders and a fake card engine; no network.

import html
import json
import re
import sys
import unittest
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import Clock, FakeEngine, cfg_in_tmp, serve, slot_posted  # noqa: E402

from clipfactory import cards as CD             # noqa: E402
from clipfactory import cardspage as CP         # noqa: E402
from clipfactory import config as C             # noqa: E402
from clipfactory import registry as REG         # noqa: E402
from clipfactory import store                   # noqa: E402
from clipfactory import switches as SW          # noqa: E402

DAY = date(2026, 11, 2)


def outside_code(page: str) -> str:
    return re.sub(r"<script>.*?</script>", "", re.sub(r"<style>.*?</style>", "", page, flags=re.S), flags=re.S)


class CardsPageTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        eng = FakeEngine.script(self.tmp / "engine")
        for p in (mock.patch.object(CD, "ENGINE", eng),
                  mock.patch.dict(os.environ, {"CLIP_ENGINE_PYTHON": sys.executable}),
                  mock.patch.object(CD, "_to_mp4", lambda j, m, timeout=120: Path(m).write_bytes(b"mp4"))):
            p.start()
            self.addCleanup(p.stop)
        self.clock = Clock(self.cfg.slot_time(DAY, "06:40")).patch(self, self.cfg)
        raw = {"v": 1, "modes": {"cards.make": "on"},
               "settings": {"cards.kinds": ["countdown", "week_best"],
                            "cards.targets": ["facebook_story", "instagram_story", "facebook_feed"]}}
        store.write(SW.file_path(self.gta), raw)
        for i, k in enumerate(("07:00", "08:45", "10:30")):
            slot_posted(self, self.gta, DAY - timedelta(days=i + 1), k)
            from clipfactory import board as B
            with B.edit(self.cfg, self.gta, DAY - timedelta(days=i + 1)) as bd:
                for o in bd["slots"][k]["outlets"].values():
                    o["metrics"] = {"views": 100 * (i + 1)}
        CD.make(self.cfg, self.gta, DAY, self.clock())
        self.get, self.post = serve(self, self.cfg)

    def page(self, q=""):
        code, headers, body = self.get("/cards" + q)
        self.assertEqual(code, 200)
        return headers, body.decode()

    def seen(self, cid):
        return CD.card_hash(CD.get_card(self.gta, DAY, cid))

    # ------------------------------------------------------------------------------------------ the page
    def test_1_the_tab_the_page_and_its_previews(self):
        headers, text = self.page()
        self.assertIn("img-src 'self'", headers.get("Content-Security-Policy", ""))
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")
        self.assertIn('<a href="/cards" aria-current=page>Cards</a>', text)
        self.assertIn("<title>Clip Review · Cards</title>", text)
        self.assertIn('class="sx sx-cards"', text)
        story = re.search(r'<div class="cd-prev cd-story"><img src="([^"]+)"', text)
        self.assertIsNotNone(story)
        strip = re.search(r'<div class="cd-prev cd-strip"[^>]*>(.*?)</div>', text, re.S)
        self.assertEqual(strip.group(1).count("<img"), 4)
        for src in re.findall(r'<img src="([^"]+)"', text):
            src = html.unescape(src)
            self.assertTrue(src.startswith("/file?p="), src)
            p = Path(urllib.parse.unquote(urllib.parse.parse_qs(urllib.parse.urlparse(src).query)["p"][0]))
            self.assertIn(C.STATE.resolve(), p.resolve().parents)
            code, h, body = self.get(src)
            self.assertEqual(code, 200, src)
            self.assertEqual(body, p.read_bytes())
        spec = str(CD.card_dir(self.gta, DAY, "countdown-0900") / "spec.json")
        self.assertEqual(self.get("/file?p=" + urllib.parse.quote(spec))[0], 404)      # never a JSON file
        self.assertIn("Approve all 2", text)
        self.assertIn("Standing rule: story reposts", text)
        self.assertIn("Instagram stories on this account", text)
        self.assertIn("factory.py cards probe gaming", text)

    def test_2_the_data_and_its_stamp(self):
        code, _, body = self.get(f"/cards/data?date={DAY}")
        d = json.loads(body)
        self.assertEqual((code, d["day"]), (200, DAY.isoformat()))
        self.assertIn("cd-card", d["html"])
        again = json.loads(self.get(f"/cards/data?date={DAY}")[2])
        self.assertEqual(again["stamp"], d["stamp"])
        CD.decline(self.cfg, self.gta, DAY, "countdown-0900", [], "", "Andrew")
        self.assertNotEqual(json.loads(self.get(f"/cards/data?date={DAY}")[2])["stamp"], d["stamp"])
        far = json.loads(self.get("/cards/data?date=2026-12-25")[2])
        self.assertEqual(far["day"], (DAY + timedelta(days=1)).isoformat())          # today and tomorrow ahead

    def test_3_the_copy_is_plain(self):
        _, text = self.page()
        words = outside_code(text).replace("<!doctype html>", "")
        for bad in ("\u2014", "\u2013", "!"):
            self.assertNotIn(bad, words)
        for bad in ("\u2014", "\u2013"):
            self.assertNotIn(bad, CP.JS + CP.CSS)

    def test_4_confirms_name_the_words_and_where_they_go(self):
        _, text = self.page()
        asks = [html.unescape(a) for a in re.findall(r'data-ask="([^"]*)"', text)]
        approve = next(a for a in asks if a.startswith("Sign this GTA 6 countdown"))
        self.assertIn("Sign this GTA 6 countdown for Gaming? It goes to Facebook story and Instagram story at 09:00. "
                      "The picture and words go exactly as shown here.", approve)
        self.assertIn("Each platform takes 5 card posts a day at the most", next(
            a for a in asks if a.startswith("Sign all 2 cards for Gaming")))
        self.assertTrue(any(a.startswith("Make this GTA 6 countdown for Gaming again?") for a in asks), asks)
        self.assertIn("Decline this GTA 6 countdown for Gaming? It will not post to Facebook story, Instagram story.",
                      asks)
        self.assertIn('data-name="Gaming"', text)                      # the rule box: its confirm names the account
        self.assertIn("'Sign the story repost rule for ' + (f.dataset.name", CP.JS)
        self.assertIn("'Move the ' + (card.dataset.name", CP.JS)
        self.assertIn('data-name="GTA 6 countdown for Gaming"', text)
        every = next(a for a in asks if a.startswith("Sign all 2 cards for Gaming"))
        self.assertIn("GTA 6 countdown at 09:00 to Facebook story and Instagram story", every)
        self.assertIn("Week's best at 19:00 to Facebook post", every)
        self.assertIn("goes to that platform\\'s story", CP.JS)

    # ------------------------------------------------------------------------------------------ the clicks
    def test_5_every_click_needs_the_page(self):
        bodies = {"card-approve": {"id": "countdown-0900", "seen": self.seen("countdown-0900")},
                  "card-decline": {"id": "countdown-0900", "reasons": []}, "card-remake": {"id": "countdown-0900"},
                  "card-time": {"id": "countdown-0900", "at": "10:00"},
                  "card-approve-all": {"seen": {"countdown-0900": self.seen("countdown-0900")}},
                  "card-rule-sign": {"fields": {}}, "card-rule-pause": {}}
        self.assertEqual(set(bodies), set(CP.WHAT))
        for op, extra in bodies.items():
            body = dict({"op": op, "account": "gaming", "date": DAY.isoformat()}, **extra)
            code, got = self.post(body, page=False)
            self.assertEqual(code, 400, op)
            self.assertTrue(got["message"].startswith("Only a click on the review page can"), got)
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "ready")
        self.assertEqual(CD.read_rule(self.gta)["state"], "unsigned")

    def test_6_approve_decline_remake_and_time_from_the_page(self):
        _, text = self.page()
        seen = re.search(r'data-id="countdown-0900" data-key="([0-9a-f]+)"', text).group(1)
        code, got = self.post({"op": "card-approve", "account": "gaming", "date": DAY.isoformat(),
                               "id": "countdown-0900", "seen": "0" * 64})
        self.assertEqual(code, 400)
        self.assertIn("changed after the page showed it", got["message"])
        code, got = self.post({"op": "card-approve", "account": "gaming", "date": DAY.isoformat(),
                               "id": "countdown-0900", "seen": seen})
        self.assertEqual((code, got["ok"]), (200, True), got)
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["signoff"]["by"], "Andrew")
        code, got = self.post({"op": "card-time", "account": "gaming", "date": DAY.isoformat(),
                               "id": "countdown-0900", "at": "10:30"})
        self.assertIn("Approve it again", got["message"])
        code, got = self.post({"op": "card-remake", "account": "gaming", "date": DAY.isoformat(),
                               "id": "countdown-0900"})
        self.assertEqual(code, 200, got)
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(c["at"], self.cfg.slot_time(DAY, "10:30").astimezone().astimezone(
            __import__("datetime").timezone.utc).isoformat())           # Make again keeps his time
        code, got = self.post({"op": "card-decline", "account": "gaming", "date": DAY.isoformat(),
                               "id": "countdown-0900", "reasons": ["looks_off"], "note": "busy"})
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "declined")
        self.clock.at("21:30")
        code, got = self.post({"op": "card-remake", "account": "gaming", "date": DAY.isoformat(),
                               "id": "countdown-0900"})
        self.assertEqual(code, 400)
        self.assertIn("06:26", got["message"])

    def test_7_approve_all_and_the_rule(self):
        _, text = self.page()
        seen = json.loads(html.unescape(re.search(r'data-op="card-approve-all" data-seen="([^"]+)"', text).group(1)))
        code, got = self.post({"op": "card-approve-all", "account": "gaming", "date": DAY.isoformat(),
                               "seen": seen})
        self.assertEqual(code, 200, got)
        self.assertIn("Signed 2", got["message"])
        code, got = self.post({"op": "card-rule-sign", "account": "gaming",
                               "fields": {"platforms": ["instagram"], "which": "all", "per_day": 3,
                                          "delay_min": [40, 55]}, "version": "1"})
        self.assertEqual(code, 200, got)
        rule = CD.read_rule(self.gta)
        self.assertTrue(CD.rule_signed(rule))
        self.assertEqual((rule["platforms"], rule["per_day"], rule["version"]), (["instagram"], 3, 2))
        _, text = self.page()
        self.assertIn("Pause story reposts for Gaming? None is queued until the rule is signed again.",
                      [html.unescape(a) for a in re.findall(r'data-ask="([^"]*)"', text)])
        code, got = self.post({"op": "card-rule-pause", "account": "gaming"})
        self.assertEqual(CD.read_rule(self.gta)["state"], "paused")
        code, got = self.post({"op": "card-approve", "account": "nobody", "date": DAY.isoformat(), "id": "x",
                               "seen": "x"})
        self.assertEqual(code, 400)
        self.assertIn("not one of your accounts", got["message"])

    def test_8_the_phone_rules(self):
        css = CP.CSS
        phone = css[css.index("@media (max-width:600px)"):]
        self.assertIn(".cd-grid{grid-template-columns:minmax(0,1fr)}", phone)
        self.assertIn(".cd-story img{max-height:58vh}", phone)
        self.assertIn(".cd-r{height:40px;min-width:40px}", phone)
        self.assertIn("font-size:16px", phone)
        self.assertIn("scroll-snap-type:x mandatory", css)
        self.assertIn("aspect-ratio:9/16", css)
        self.assertNotIn("gradient", css)
        self.assertNotIn("box-shadow", css)
        _, text = self.page()
        self.assertIn("<meta name=viewport content='width=device-width,initial-scale=1'>", text)
        for cls in re.findall(r'class="([^"]+)"', outside_code(text)):
            for c in cls.split():
                if c.startswith("cd-"):
                    self.assertIn("." + c, css, c)

    def test_9_a_part_that_will_not_import_hides_only_itself(self):
        self.assertIn(("cards", "/cards", "Cards"), REG.tab_pages())
        self.assertEqual(REG.act_module("card-approve"), CP)
        self.assertEqual(REG.fn("cards:tick"), CD.tick)
        real_import, real_find = REG.importlib.import_module, REG.importlib.util.find_spec

        def broken(name, *a, **kw):
            if name == "clipfactory.cardspage":
                raise ImportError("cardspage broke")
            return real_import(name, *a, **kw)

        def absent(name, *a, **kw):
            return None if name == "clipfactory.cardspage" else real_find(name, *a, **kw)
        self.addCleanup(REG.reset)
        for how, patch, want in (("broken", mock.patch.object(REG.importlib, "import_module", broken),
                                  (500, b"error: ImportError: cardspage broke")),
                                 ("not built", mock.patch.object(REG.importlib.util, "find_spec", absent),
                                  (404, b"not built yet"))):
            REG.reset()
            with mock.patch.dict(sys.modules), patch:
                sys.modules.pop("clipfactory.cardspage", None)
                code, _, body = self.get("/cards")
                self.assertEqual((code, body), want, how)
                for path in ("/", "/stats"):
                    code, _, text = self.get(path)
                    self.assertEqual(code, 200, (how, path))
                    self.assertIsNone(re.search(r'href="/cards"[^>]*>Cards</a>', text.decode()), how)   # no tab
                code, got = self.post({"op": "card-approve", "account": "gaming", "date": DAY.isoformat(),
                                       "id": "countdown-0900", "seen": "x"})
                self.assertEqual(code, 400, how)
                self.assertIn("not built yet", got["message"], how)
        REG.reset()
        self.assertEqual(self.get("/cards")[0], 200)

    # ------------------------------------------------------------------------------------------ the review's fixes
    def test_10_notes_show_the_local_time_and_the_words(self):
        CD.approve(self.cfg, self.gta, DAY, "countdown-0900", self.seen("countdown-0900"), "Andrew", self.clock())
        _, text = self.page()
        notes = [html.unescape(n) for n in re.findall(r'<div class="note">([^<]*)</div>', text)]
        self.assertTrue(notes)
        self.assertTrue(all(not n.startswith("0:00") for n in notes), notes)
        self.assertTrue(any(re.fullmatch(r"\d\d:\d\d signed by Andrew", n) for n in notes), notes)
        self.assertEqual(CP.note_words(self.cfg, "2026-11-02T13:57:15+00:00 declined by Andrew (Looks off): busy"),
                         CD._local(self.cfg, CD._when("2026-11-02T13:57:15+00:00")).strftime("%H:%M")
                         + " declined by Andrew (Looks off): busy")
        self.assertEqual(CP.note_words(self.cfg, "plain words"), "plain words")

    def test_11_a_queued_card_offers_decline_and_a_new_time(self):
        CD.approve(self.cfg, self.gta, DAY, "countdown-0900", self.seen("countdown-0900"), "Andrew", self.clock())
        with CD.edit_board(self.gta, DAY) as bd:
            bd["cards"]["countdown-0900"]["status"] = "scheduled"
        _, text = self.page()
        art = re.search(r'<article[^>]*data-id="countdown-0900".*?</article>', text, re.S).group(0)
        self.assertIn('data-op="card-decline-open"', art)
        self.assertIn('data-op="card-time-open"', art)
        self.assertNotIn('data-op="card-remake"', art)
        code, got = self.post({"op": "card-decline", "account": "gaming", "date": DAY.isoformat(),
                               "id": "countdown-0900", "reasons": ["not_needed"], "note": ""})
        self.assertEqual(code, 200, got)
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "declined")

    def test_12_why_not_made_and_the_real_time(self):
        with CD.edit_board(self.gta, DAY) as bd:
            bd["made"]["skipped"] = [{"kind": "clip_of_day", "words": "clip_of_day: no clip with a title went out "
                                                                       "yesterday"}]
        from social_fakes import slot_ready
        slot_ready(self, self.gta, DAY, "08:45")
        _, text = self.page()
        self.assertIn("Not made this day (1)", text)
        self.assertIn("no clip with a title went out yesterday", text)
        self.assertIn("Goes about 09:15 (the 08:45 Reel keeps 30 minutes clear)", text)
        asks = [html.unescape(a) for a in re.findall(r'data-ask="([^"]*)"', text)]
        self.assertTrue(any("at 09:00, about 09:15 (the 08:45 Reel keeps 30 minutes clear)" in a for a in asks), asks)


    def test_13_the_empty_box_is_flat_like_the_kit(self):
        css = CP.CSS
        for bad in ("dashed", "dotted", "gradient", "glow", "box-shadow"):
            self.assertNotIn(bad, css)
        rule = re.search(r"\.cd-empty\{([^}]*)\}", css).group(1)
        self.assertNotIn("border", rule)
        from clipfactory import socialui as SX
        kit = re.search(r"\.sx-empty\{([^}]*)\}", SX.CSS).group(1)
        self.assertEqual(rule, kit)
        with CD.edit_board(self.gta, DAY) as bd:
            bd["cards"] = {}
        _, text = self.page()
        self.assertIn('<div class="cd-empty">No card for this day', text)


if __name__ == "__main__":
    unittest.main()
