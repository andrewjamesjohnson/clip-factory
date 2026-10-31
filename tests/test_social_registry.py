import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The social layer's plug board (registry.py) and page kit (socialui.py) on the review page: a part's tab, its /data
# and its own GETs, its clicks, the Today band, card parts, health lines and calendar items, and a part that is not
# built or will not import hiding only itself. Stand-in modules only; nothing reaches the network.

import contextlib
import importlib.abc
import importlib.machinery
import io
import json
import shutil
import subprocess
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B          # noqa: E402
from clipfactory import collect as K        # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import review as R         # noqa: E402
from clipfactory import socialui as SX      # noqa: E402

DAY = date(2031, 7, 8)
FAKE = REG.Page("sxfake", "/sxfake", "Fakes", "sxfakepage", None)
CHILD = REG.Page("sxchild", "/sxfake/child", "Child", "sxchildpage", "sxfake")
GONE = REG.Page("sxgone", "/sxgone", "Gone", "sxgonepage", None)
BROKE = REG.Page("sxbroke", "/sxbroke", "Broke", "sxbrokepage", None)


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class _Broken(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Finds clipfactory.sxbrokepage, then fails to import it: a part with a bug in it."""

    def find_spec(self, name, path=None, target=None):
        if name == "clipfactory.sxbrokepage":
            return importlib.machinery.ModuleSpec(name, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise RuntimeError("a bug in the part")


def fake_page_module(name: str) -> types.ModuleType:
    mod = types.ModuleType("clipfactory." + name)

    def page(cfg, tok, base_css, tabs_html, switch_html, acct, q):
        return SX.shell(cfg, name, "Fakes", tok, base_css, tabs_html, switch_html, acct,
                        f'<p id="fake">{name} for {acct}</p>', subnav=SX.subnav("sxfake", "sxfake", acct,
                                                                                (("sxfake", "/sxfake", "Own"),)))

    def data(cfg, q, now=None):
        return SX.stamp({"name": name, "q": q})

    def get(cfg, sub, q):
        return 200, "text/plain; charset=utf-8", f"{name} got {sub}".encode()

    def act(cfg, body, page=False, now=None):
        SX.need_page(page, "do this")
        return True, f"{name} did {body.get('op')}"
    mod.page, mod.data, mod.get, mod.act = page, data, get, act
    return mod


class RegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-registry-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["review_port"] = 0
        for a in raw["accounts"].values():
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            # The account and skill folders too: collect reads <account_dir>/Publish/schedule.json and
            # <skill_dir>/work/<job>/project.json for a clip's provenance, and those must never be Andrew's live ones.
            a["account_dir"] = str(cls.tmp / "accounts" / a["name"])
            a["skill_dir"] = str(cls.tmp / "skills" / Path(os.path.expanduser(a["skill_dir"])).name)
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.master = cls.tmp / "master.mp4"
        clip(cls.master, 2.0)

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens

    def setUp(self):
        for d in ("state", "drafts", "delivered", "keys"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg = C.load(self.cfg_path)
        self.gta = self.cfg.account("gaming")
        REG.reset()
        self.addCleanup(REG.reset)
        for p in (mock.patch.object(REG, "PAGES", (FAKE, CHILD, GONE, BROKE)),
                  mock.patch.object(REG, "ACT_PREFIXES", (("sxf-", "sxfakepage"), ("sxg-", "sxgonepage"))),
                  mock.patch.object(REG, "SUBROUTES", {"/stats/sxseries": "sxfakepage:series"}),
                  mock.patch.object(REG, "COMMANDS", {"sxcmd": "sxfakepage", "sxnone": "sxgonepage",
                                                      "sxbad": "sxbrokepage"})):
            p.start()
            self.addCleanup(p.stop)
        self.inject("sxfakepage", fake_page_module("sxfakepage"))
        self.inject("sxchildpage", fake_page_module("sxchildpage"))
        finder = _Broken()
        sys.meta_path.insert(0, finder)
        self.addCleanup(sys.meta_path.remove, finder)

    def inject(self, name: str, mod) -> None:
        """A stand-in for clipfactory.<name> (None: as not built) until the test ends, then the real one back, in
        sys.modules AND as the package's attribute: a later test (or file) that imports it gets the module its own
        top-level import holds, whatever order the tests run in."""
        key = "clipfactory." + name
        pkg = sys.modules["clipfactory"]
        had_mod, old_mod = key in sys.modules, sys.modules.get(key)
        had_attr, old_attr = hasattr(pkg, name), getattr(pkg, name, None)

        def restore():
            if had_mod:
                sys.modules[key] = old_mod
            else:
                sys.modules.pop(key, None)
            if had_attr:
                setattr(pkg, name, old_attr)
            elif hasattr(pkg, name):
                delattr(pkg, name)
            REG.reset()
        self.addCleanup(restore)
        sys.modules[key] = mod

    def ready(self, *slots: str) -> None:
        root = self.gta.delivered_dirs(DAY)[0]
        for k in slots:
            d = root / f"{k.replace(':', '')} clip-{k.replace(':', '')}"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.master, d / "01-x.mp4")
            for p in ("youtube", "tiktok", "facebook", "reels"):
                (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
        K.collect(self.cfg, self.gta, DAY)

    def serve(self):
        srv = R.Server(("127.0.0.1", 0), R.make_handler(self.cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        hosts = {"Host": f"127.0.0.1:{self.cfg.review_port}"}
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        tok = R.token()

        def get(path, headers=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={**hosts, **(headers or {})})
            try:
                with direct.open(req, timeout=10) as r:
                    return r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read()

        def post(body, origin=True):
            h = {**hosts, "Content-Type": "application/json", "X-Token": tok}
            if origin:
                h.update({"Origin": f"http://127.0.0.1:{self.cfg.review_port}", "Sec-Fetch-Site": "same-origin"})
            req = urllib.request.Request(f"http://127.0.0.1:{port}/act", data=json.dumps(body).encode(), method="POST",
                                         headers=h)
            try:
                with direct.open(req, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        return get, post

    # ---------------------------------------------------------------------------------------------- the plug board
    def test_0_every_account_folder_is_the_tests_own(self):
        # Regression: account_dir was left real, so collect read the live Gaming Publish/schedule.json for a clip's
        # provenance. Every account's folders sit in the test's temp folder, and so does every schedule collect reads.
        tmp = self.tmp.resolve()
        for acct in list(self.cfg.all_accounts.values()) + list(self.cfg.accounts.values()):
            for where in (acct.account_dir, acct.skill_dir):
                self.assertTrue(Path(where).resolve().is_relative_to(tmp), where)
        real, seen = Path.read_text, []

        def spy(path, *a, **k):
            seen.append(Path(path))
            return real(path, *a, **k)

        with mock.patch.object(Path, "read_text", spy):
            self.ready("07:00")
        schedules = [q for q in seen if q.name == "schedule.json"]
        self.assertTrue(schedules)      # the provenance lookup ran, so the check below means something
        self.assertEqual([q for q in schedules if not q.resolve().is_relative_to(tmp)], [])

    def test_1_load_present_fn_and_a_broken_part(self):
        self.assertIs(REG.load("sxfakepage"), sys.modules["clipfactory.sxfakepage"])
        self.assertIsNone(REG.load("sxgonepage"))
        self.assertNotIn("sxgonepage", REG.BROKEN)
        self.assertIsNone(REG.load("sxbrokepage"))
        self.assertEqual(REG.BROKEN["sxbrokepage"], "RuntimeError: a bug in the part")
        self.assertTrue(REG.present("sxfakepage"))
        self.assertFalse(REG.present("sxbrokepage"))
        self.assertIsNotNone(REG.fn("sxfakepage:data"))
        self.assertIsNone(REG.fn("sxfakepage:nothing"))
        self.assertIsNone(REG.fn("sxgonepage:data"))

    def test_2_tabs_show_only_built_parts_and_a_child_marks_its_tab(self):
        self.assertEqual(REG.tab_pages(), [("clips", "/", "Clips"), ("stats", "/stats", "Stats"),
                                           ("sxfake", "/sxfake", "Fakes")])       # Gone not built, Broke broken
        self.assertEqual(R.tabs("sxchild", "gaming"),
                         '<nav class="tabs" aria-label="Pages"><a href="/?acct=gaming">Clips</a>'
                         '<a href="/calendar?acct=gaming">Calendar</a><a href="/stats?acct=gaming">Stats</a>'
                         '<a href="/money?acct=gaming">Money</a>'
                         '<a href="/sxfake?acct=gaming" aria-current=page>Fakes</a></nav>')
        self.assertEqual(REG.top_of("sxchild"), "sxfake")
        self.assertEqual(REG.url_of("sxchild"), "/sxfake/child")
        self.assertEqual(REG.url_of("clips"), "/")
        self.assertEqual([p.key for p in REG.children("sxfake")], ["sxchild"])
        self.assertIn('href="/sxfake?acct=gaming"', R.switch(self.cfg, "sxfake", "all"))   # the switch stays put
        self.assertIn('href="/stats?acct=gaming"', R.switch(self.cfg, "stats", "all"))
        self.assertIn('href="/?acct=gaming&amp;date=2031-07-08"', R.switch(self.cfg, "clips", "all", DAY))

    def test_3_route_tries_the_longest_url_first(self):
        self.assertEqual(REG.route("/sxfake"), ("page", FAKE, ""))
        self.assertEqual(REG.route("/sxfake/data"), ("data", FAKE, ""))
        self.assertEqual(REG.route("/sxfake/child"), ("page", CHILD, ""))
        self.assertEqual(REG.route("/sxfake/child/data"), ("data", CHILD, ""))
        self.assertEqual(REG.route("/sxfake/thing/1"), ("get", FAKE, "thing/1"))
        self.assertEqual(REG.route("/stats/sxseries"), ("sub", None, "sxfakepage:series"))
        self.assertIsNone(REG.route("/sxfakes"))
        self.assertIsNone(REG.route("/"))
        self.assertIsNone(REG.route("/file"))

    def test_4_the_page_its_data_and_its_gets_are_served(self):
        get, _ = self.serve()
        code, headers, body = get("/sxfake?acct=gaming")
        self.assertEqual(code, 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        page = body.decode()
        self.assertIn('<body class="sx sx-sxfakepage">', page)
        self.assertIn("<title>Clip Review · Fakes</title>", page)
        self.assertIn('<a href="/sxfake?acct=gaming" aria-current=page>Fakes</a>', page)       # its tab
        self.assertIn('aria-current=true>Gaming</a>', page)
        self.assertIn('<nav class="sx-subnav" aria-label="Views"><a href="/sxfake?acct=gaming" aria-current=page>'
                      'Own</a><a href="/sxfake/child?acct=gaming">Child</a></nav>', page)
        self.assertIn('<p id="fake">sxfakepage for gaming</p>', page)
        self.assertIn("window.SX = {", page)
        self.assertNotIn("__TOKEN__", page)
        self.assertIn(json.dumps(R.token()), page)
        self.assertEqual(get("/sxfake", {"Host": "evil.example"})[0], 403)                    # loopback only
        code, headers, body = get("/sxfake/data?x=1")
        self.assertEqual(code, 200)
        d = json.loads(body)
        self.assertEqual((d["name"], d["q"]), ("sxfakepage", {"x": "1"}))
        self.assertEqual(len(d["stamp"]), 16)
        code, headers, body = get("/sxfake/thing/1")
        self.assertEqual((code, body), (200, b"sxfakepage got thing/1"))
        self.assertNotIn("Content-Security-Policy", headers)
        self.assertEqual(get("/sxfake/child")[0], 200)
        code, _, body = get("/sxgone")
        self.assertEqual((code, body), (404, b"not built yet"))
        self.assertEqual(get("/sxbroke")[0], 500)
        self.assertEqual(get("/stats/sxseries")[0], 404)          # its function is not there
        self.sx_series()
        code, headers, body = get("/stats/sxseries")
        self.assertEqual((code, body), (200, b"[1, 2]"))

    def sx_series(self):
        sys.modules["clipfactory.sxfakepage"].series = lambda cfg, q: (200, "application/json", b"[1, 2]")

    def test_5_a_prefixed_click_goes_to_its_part_and_knows_the_page(self):
        _, post = self.serve()
        self.assertEqual(post({"op": "sxf-do"}), (200, {"ok": True, "message": "sxfakepage did sxf-do"}))
        code, d = post({"op": "sxf-do"}, origin=False)                   # a script, not a click on the page
        self.assertEqual((code, d["message"]), (400, "Only a click on the review page can do this."))
        code, d = post({"op": "sxg-do"})
        self.assertEqual((code, d["message"]), (400, "that part of Clip Review is not built yet"))
        self.assertIsNone(REG.act_module("approve"))
        code, d = post({"op": "nothing-known", "account": "gaming", "date": DAY.isoformat()})
        self.assertEqual(code, 400)
        self.assertIn("unknown action", d["message"])

    def test_6_calls_skip_missing_parts_and_log_an_error_once(self):
        mod = sys.modules["clipfactory.sxfakepage"]
        mod.ok = lambda x: x + 1
        mod.bad = lambda x: 1 / 0
        for _ in range(3):
            got = REG.calls(("sxfakepage:ok", "sxfakepage:bad", "sxgonepage:ok", "sxbrokepage:ok"), 1)
            self.assertEqual(got, [("sxfakepage:ok", 2)])
        log = (C.STATE / "logs" / "social-errors.log").read_text().splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("sxfakepage:bad: ZeroDivisionError: division by zero", log[0])
        with mock.patch.object(REG, "CARD_CSS", ("sxgonepage", "sxbrokepage")):  # parts not built: nothing added
            self.assertEqual((REG.card_css(), REG.card_js()), ("", ""))
        mod.CARD_CSS = ".cx-x{color:red}"
        with mock.patch.object(REG, "CARD_CSS", ("sxfakepage", "sxgonepage")):
            self.assertEqual(REG.card_css(), ".cx-x{color:red}")
            self.assertEqual(REG.card_js(), "")                               # no CARD_JS: no script at all
            mod.CARD_JS = "window.sxSeen=1;"
            self.assertEqual(REG.card_js(), "\ntry{window.sxSeen=1;}catch(e){console.error('sxfakepage', e)}")

    def test_7_run_cli_missing_quiet_broken_and_built(self):
        def run(name, argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                REG.run_cli(self.cfg, name, argv)
            return out.getvalue()
        self.assertEqual(run("sxnone", ["status"]), "sxnone: not built yet\n")
        self.assertEqual(run("sxnone", ["tick", "--quiet"]), "")
        self.assertEqual(run("social", ["tick", "--quiet"]), "")             # social.py is not built in this test
        seen = []
        sys.modules["clipfactory.sxfakepage"].cli = lambda cfg, argv: seen.append(argv)
        self.assertEqual(run("sxcmd", ["a", "b"]), "")
        self.assertEqual(seen, [["a", "b"]])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ex:
            REG.run_cli(self.cfg, "sxbad", ["--quiet"])
        self.assertEqual(ex.exception.code, 1)
        self.assertIn("RuntimeError: a bug in the part", out.getvalue())

    # ---------------------------------------------------------------------------------------------- the Clips page
    def test_8_with_no_part_built_the_clips_page_is_as_before(self):
        # social and verdicts (the seam's second commit) and weekpage (the Week link, the log branch) are built
        # since: hidden here, as not built
        for name in ("social", "verdicts", "weekpage"):
            self.inject(name, None)
        self.ready("07:00")
        now = self.cfg.slot_time(DAY, "06:00")
        live = R.live(self.cfg, DAY, now)
        self.assertNotIn("brief", live)
        self.assertNotIn("items", live["cal"])
        self.assertNotIn("week", live["cal"])
        self.assertEqual(live["stamp"], R._stamp(live["sections"], live["cal"], live["needs"], live["casts"],
                                                 live["kpis"] + live["breaking"] + live["rail"]))
        page = R.page(self.cfg, DAY, now, tok="t")
        self.assertNotIn('id="brief"', page)
        self.assertIn("const REASONS=[];", page)
        self.assertNotIn("prompt('Decline", page)                          # Decline: the Reason codes panel
        self.assertIn("openChooser(b,op,account,slot)", page)                # Make again: chips

    def test_9_the_social_parts_on_the_clips_page(self):
        self.ready("07:00")
        now = self.cfg.slot_time(DAY, "06:00")
        soc = types.ModuleType("clipfactory.social")
        soc.card_parts = lambda cfg, acct, day, s, now: f'<details class="cx-x" data-k="cx">coach {s["slot"]}</details>'
        soc.health_lines = lambda cfg, now, accts: [("bad", "Instagram comments wait: Meta asked us to stop."),
                                                    ("calm", "Inbox: 2 to answer.")]
        soc.brief = lambda cfg, now, acct: [{"key": "inbox", "words": "Inbox: 2 to answer", "href": "/inbox",
                                             "level": "calm", "order": 1},
                                            {"key": "stop", "words": "Stopped", "href": "/log", "level": "bad",
                                             "order": 0}]
        soc.cal_items = lambda cfg, now, accts: {DAY.isoformat(): {"gaming": [{"k": "card", "s": "scheduled"}]}}
        self.inject("social", soc)
        self.inject("weekpage", types.ModuleType("clipfactory.weekpage"))
        pages = mock.patch.object(REG, "PAGES", REG.PAGES + (REG.Page("week", "/week", "Week", "weekpage", "clips"),))
        pages.start()                                   # the Week link: its page on the plug board, and built
        self.addCleanup(pages.stop)
        vd = types.ModuleType("clipfactory.verdicts")
        vd.CHIPS = [["weak_hook", "Weak hook"], ["framing", "Framing"]]
        self.inject("verdicts", vd)
        live = R.live(self.cfg, DAY, now)
        # since 10-29 (look.py) the Today band's chips sit in the right column, under "Also today", each a link
        self.assertNotIn("brief", live)
        self.assertTrue(live["rail"].startswith('<aside class="rail" data-h="'))
        self.assertIn('<a class="also calm" href="/inbox">Inbox: 2 to answer<span>›</span></a>'
                      '<a class="also bad" href="/log">Stopped<span>›</span></a>', live["rail"])
        card = live["sections"][0]["cards"][0]
        self.assertIn('<details class="cx-x" data-k="cx">coach 07:00</details>', card["bottom"])
        head = live["needs"]["head"]
        self.assertIn('<div class="alert health" role="alert">Instagram comments wait: Meta asked us to stop.</div>', head)
        self.assertIn('<div class="calm health">Inbox: 2 to answer.</div>', head)
        self.assertEqual(live["cal"]["items"], {DAY.isoformat(): {"gaming": [{"k": "card", "s": "scheduled"}]}})
        self.assertIs(live["cal"]["week"], True)
        self.assertEqual(live["stamp"], R._stamp(live["sections"], live["cal"], live["needs"], live["casts"],
                                                 live["kpis"] + live["breaking"] + live["rail"]))
        page = R.page(self.cfg, DAY, now, tok="t")
        self.assertIn('<main><section class="kpis"', page)                       # the day's four numbers come first
        self.assertIn('const REASONS=[["weak_hook", "Weak hook"], ["framing", "Framing"]];', page)

    def test_10_a_part_that_raises_never_takes_the_page_down(self):
        self.ready("07:00")
        now = self.cfg.slot_time(DAY, "06:00")
        soc = types.ModuleType("clipfactory.social")

        def boom(*a):
            raise RuntimeError("no")
        soc.card_parts = soc.health_lines = soc.brief = soc.cal_items = boom
        self.inject("social", soc)
        live = R.live(self.cfg, DAY, now)
        self.assertNotIn("brief", live)
        self.assertNotIn("items", live["cal"])
        self.assertEqual(len(live["sections"][0]["cards"]), 1)
        self.assertIn("Clip Review", R.page(self.cfg, DAY, now, tok="t"))
        self.assertIn("social:card_parts: RuntimeError: no", (C.STATE / "logs" / "social-errors.log").read_text())

    def test_10b_a_part_that_refuses_never_takes_the_page_down(self):
        """SystemExit is this codebase's refusal (store, socialstore, outward.queue): a card part, a health line, the
        Today band or the calendar that refuses hides only itself, and the page and /live still answer."""
        self.ready("07:00")
        now = self.cfg.slot_time(DAY, "06:00")
        soc = types.ModuleType("clipfactory.social")

        def refuse(*a):
            raise SystemExit("It is signed: Pull it to change its first comment.")
        soc.card_parts = soc.health_lines = soc.brief = soc.cal_items = refuse
        self.inject("social", soc)
        self.assertEqual(REG.calls(("social:brief",), self.cfg, now, "all"), [])
        live = R.live(self.cfg, DAY, now)
        self.assertNotIn("brief", live)
        self.assertNotIn("items", live["cal"])
        self.assertEqual(len(live["sections"][0]["cards"]), 1)
        self.assertNotIn("Cannot show", live["sections"][0]["cards"][0]["top"])
        self.assertIn("social:card_parts: SystemExit: It is signed",
                      (C.STATE / "logs" / "social-errors.log").read_text())
        get, _ = self.serve()
        code, _, body = get(f"/?date={DAY}")
        self.assertEqual(code, 200)
        self.assertIn(b"Clip Review", body)
        self.assertEqual(get(f"/live?date={DAY}")[0], 200)
        # a social page whose own GETs refuse: the words come back as a 400, never a dropped connection
        mod = sys.modules["clipfactory.sxfakepage"]
        mod.data = mod.get = refuse
        mod.page = refuse
        for path in ("/sxfake/data", "/sxfake/thing/1", "/sxfake"):
            code, _, body = get(path)
            self.assertEqual((code, body), (400, b"It is signed: Pull it to change its first comment."), path)

    def test_11_decline_takes_reasons(self):
        self.ready("07:00", "08:45")
        _, post = self.serve()
        code, d = post({"op": "decline", "account": "gaming", "date": DAY.isoformat(), "slots": ["07:00"],
                        "reason": "", "reasons": "weak_hook"})
        self.assertEqual((code, d["message"]), (400, "reasons must be a list of reason codes"))
        code, d = post({"op": "decline", "account": "gaming", "date": DAY.isoformat(), "slots": ["07:00"],
                        "reason": "too dark", "reasons": ["weak_hook", "framing"]})
        self.assertEqual(code, 200)
        self.assertTrue(d["ok"])
        s = B.read(self.gta, DAY)["slots"]["07:00"]
        self.assertEqual(s["status"], B.DECLINED)
        self.assertIn("too dark (reasons: hook, framing)", B.last_note(s))       # weak_hook is read as hook
        code, d = post({"op": "decline", "account": "gaming", "date": DAY.isoformat(), "slots": ["08:45"],
                        "reason": "too long"})                              # the old way, as before
        self.assertEqual(code, 200)
        self.assertTrue(B.last_note(B.read(self.gta, DAY)["slots"]["08:45"]).endswith(": too long"))

    # ---------------------------------------------------------------------------------------------- the page kit
    def test_12_banner_says_stop_and_running_holds_only(self):
        self.assertEqual(SX.banner(self.cfg), "")                    # no stop and no hold
        ow = types.ModuleType("clipfactory.outward")
        ow.stopped = lambda cfg: ""
        self.inject("outward", ow)
        self.assertEqual(SX.banner(self.cfg), "")
        now = datetime(2031, 7, 8, 12, 0, tzinfo=timezone.utc)
        p = C.STATE / "gaming" / "outward" / "holds.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"holds": {
            "instagram|comment": {"until": (now + timedelta(hours=3)).isoformat(), "why": "Meta said 368"},
            "facebook|publish": {"until": (now - timedelta(hours=1)).isoformat(), "why": "over"}}}))
        out = SX.banner(self.cfg, now)
        self.assertIn('<div class="sx-banner warn">Gaming on Instagram: comments and replies paused until Jul 08 ', out)
        self.assertIn(". Meta said 368.</div>", out)
        self.assertNotIn("over", out)
        ow.stopped = lambda cfg: "Stopped by Andrew: nothing social goes out."
        self.assertTrue(SX.banner(self.cfg, now).startswith(
            '<div class="sx-banner bad" role="alert">Stopped by Andrew: nothing social goes out.</div>'))
        ow.stopped = lambda cfg: 1 / 0
        self.assertEqual(SX.banner(self.cfg, now), "")

    def test_13_kit_helpers(self):
        self.assertEqual([a.slug for a in SX.own_accounts(self.cfg, "gaming")], ["gaming"])
        self.assertIn("politics", [a.slug for a in SX.own_accounts(self.cfg)])
        with self.assertRaises(SystemExit) as ex:
            SX.need_page(False, "send a reply")
        self.assertEqual(str(ex.exception), "Only a click on the review page can send a reply.")
        SX.need_page(True, "send a reply")
        a, b = SX.stamp({"x": 1}), SX.stamp({"x": 1, "stamp": "old"})
        self.assertEqual(a["stamp"], b["stamp"])
        self.assertNotEqual(a["stamp"], SX.stamp({"x": 2})["stamp"])
        self.assertEqual(SX.file_url(str(self.master)), R._file_url(str(self.master)))
        self.assertEqual(SX.brief_html([]), "")
        self.assertEqual(SX.brief_html([{"words": "Cards: 2 to sign", "href": "/cards", "level": "warn"},
                                        {"words": "x", "href": "https://evil.example", "level": "calm"}]),
                         '<a class="warn" href="/cards" title="Cards: 2 to sign">Cards: 2 to sign</a>'
                         '<span title="x">x</span>')
        self.assertEqual(SX.subnav("sxgone", "x", "all"), "")
        for css in (SX.CSS, SX.CLIPS_CSS):
            self.assertIn("@media (max-width:600px)", css)
            self.assertNotIn("gradient", css)
        self.assertIn(".tabs{overflow-x:auto;flex-wrap:nowrap;max-width:100%;scrollbar-width:none}", SX.CLIPS_CSS)
        self.assertIn("body.sx .seg button{height:40px}", SX.CSS)
        self.assertIn(SX.CLIPS_CSS, R.CSS)

    def test_14_phones_the_week_date_and_the_account_on_today_chips(self):
        # on phones the calendar's head wraps, the Week link and the account switch are 40 px targets, and the
        # TikTok caption box is 16 px (no zoom on focus)
        phone = SX.CLIPS_CSS.split("@media (max-width:600px){", 1)[1]
        for rule in (".cal-head{flex-wrap:wrap}", ".cal-weeklink{display:inline-flex;align-items:center;"
                     "justify-content:center;height:40px;min-width:40px}", ".switch a{height:40px}",
                     "input,textarea,.tt textarea,.dx-note{font-size:16px}",
                     # 2026-10-20: the day picker wraps and shrinks, so the Clips page fits 280 px
                     ".days{flex:1 1 100%;flex-wrap:wrap;min-width:0}",
                     ".days select{flex:1 1 90px;min-width:0;max-width:100%}",
                     "#needs-grid,.grid,.grid.big{grid-template-columns:minmax(0,1fr)}"):
            self.assertIn(rule, phone)
        # a long Today chip is cut with an ellipsis inside the band, never wider than the screen
        self.assertIn("max-width:100%;overflow:hidden;text-overflow:ellipsis", SX.CLIPS_CSS.split(".sx-brief a,", 1)[1]
                      .split("}", 1)[0])
        self.assertLess(R.CSS.index(".tt textarea{font:inherit;font-size:12.5px"), R.CSS.index(SX.CLIPS_CSS))
        # the account switch on a page with its own day (Week) keeps the day
        week = REG.Page("sxweek", "/sxweek", "Week", "sxfakepage", "clips")
        with mock.patch.object(REG, "PAGES", (FAKE, CHILD, GONE, BROKE, week)):
            sw = R.switch(self.cfg, "sxweek", "gaming", date(2026, 11, 2))
            self.assertIn('href="/sxweek?acct=politics&amp;date=2026-11-02"', sw)
            self.assertIn('href="/sxweek?date=2026-11-02">All</a>', sw)
            get, _ = self.serve()
            code, _, body = get("/sxweek?date=2026-11-02&acct=gaming")
            self.assertEqual(code, 200)
            self.assertIn('href="/sxweek?acct=politics&amp;date=2026-11-02"', body.decode())
            code, _, body = get("/sxweek?date=not-a-day&acct=gaming")
            self.assertEqual(code, 200)
            self.assertIn('href="/sxweek?acct=politics"', body.decode())
        self.assertNotIn("date=", R.switch(self.cfg, "stats", "all"))
        # the Today band's chips keep the account picked
        chips = [{"words": "3 new comments", "href": "/inbox", "level": "warn"},
                 {"words": "2 questions", "href": "/inbox?f=q"}, {"words": "Needs", "href": "/#needs"}]
        self.assertEqual(SX.brief_html(chips, "gaming"),
                         '<a class="warn" href="/inbox?acct=gaming" title="3 new comments">3 new comments</a>'
                         '<a href="/inbox?f=q&amp;acct=gaming" title="2 questions">2 questions</a>'
                         '<a href="/?acct=gaming#needs" title="Needs">Needs</a>')
        self.assertEqual(SX.brief_html(chips[:1], "all"),
                         '<a class="warn" href="/inbox" title="3 new comments">3 new comments</a>')
        self.ready("07:00")
        soc = types.ModuleType("clipfactory.social")
        soc.brief = lambda cfg, now, acct: chips[:1]
        self.inject("social", soc)
        live = R.live(self.cfg, DAY, self.cfg.slot_time(DAY, "06:00"), acct="gaming")
        self.assertIn('<a class="also warn" href="/inbox?acct=gaming">3 new comments<span>›</span></a>', live["rail"])


    def test_15_card_parts_on_the_clips_page_script(self):
        """2026-10-20 (the builders' reports): a card part's button posts the card's own day (a card in the Needs band
        can be another day's), falls back to the card's account and slot, fills {words} in its confirm from the
        form's data-w fields, and a card rebuilt for a new key keeps its open folds; a part's CARD_JS runs after the
        page's own script."""
        js = R.JS
        branch = js.split("if(b.dataset.x!==undefined){", 1)[1].split("if(op==='decline'", 1)[0]
        for bit in ("if(c&&c.dataset.date)body.date=c.dataset.date;", "account:account||(c?c.dataset.acct:'')",
                    "slot||(c?c.dataset.slot:'')", "let ask=b.dataset.askT||b.dataset.ask||'';",
                    "ask.split('{words}').join(words.join(' / '))", "querySelectorAll('[data-w]')"):
            self.assertIn(bit, branch)
        rec = js.split("function reconcile(grid,cards){", 1)[1].split("function apply(d){", 1)[0]
        self.assertIn("const open=[...el.querySelectorAll('details[open][data-k]')].map(d=>d.dataset.k)", rec)
        self.assertIn("if(m)m.open=true", rec)
        self.assertNotIn(chr(92), js)                                       # the page's script is not a raw string
        self.ready("07:00")
        now = self.cfg.slot_time(DAY, "06:00")
        mod = sys.modules["clipfactory.sxfakepage"]
        mod.CARD_JS = "window.sxCard=1;"
        with mock.patch.object(REG, "CARD_CSS", ("sxfakepage",)):
            page = R.page(self.cfg, DAY, now, tok="t")
        self.assertIn("try{window.sxCard=1;}catch(e){console.error('sxfakepage', e)}</script></body></html>", page)

if __name__ == "__main__":
    unittest.main()
