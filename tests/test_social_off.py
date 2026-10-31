import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Off means off (2026-10-19): with every social switch off on every account, the factory is what it was before the
# social layer. Each surface is compared with the same surface when the plug board is empty (every registry list
# patched to nothing, which is the shape before the social layer): /live for all and each account, /stats/data, the
# morning notice, the Clips page outside its <style> and <script>. The poster makes no call a platform did not make
# before and writes no social file, collect writes no extras, the doctor's social section says only "--", and the
# outward guard answers nothing. Throwaway folders; fake platforms; no network.

import argparse
import contextlib
import io
import json
import re
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import Clock, FakeGraph, FakeYouTube, cfg_in_tmp, patch_http, slot_posted, slot_ready  # noqa: E402

from clipfactory import board as B          # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import hermes as H         # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import outward as OW       # noqa: E402
from clipfactory import poster as P         # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import review as R         # noqa: E402
from clipfactory import social as SOC       # noqa: E402
from clipfactory import statspage as SP     # noqa: E402
from clipfactory import switches as SW      # noqa: E402
from clipfactory import timer as TM         # noqa: E402
import factory as F                         # noqa: E402

DAY = date(2031, 7, 8)
LISTS = ("PAGES", "SUBROUTES", "ACT_PREFIXES", "CARD_PARTS", "CARD_CSS", "HEALTH", "SUMMARIES", "MORNING_HOLDS",
         "MORNING", "DOCTORS", "AFTER_TAKE", "FOLLOW_UPS", "AFTER_POLL", "EXECUTORS", "JOBS", "COMMANDS",
         "SOCIAL_ACTIONS", "CAL_ITEMS")


@contextlib.contextmanager
def bare():
    """The plug board with nothing on it: every registry list empty (a dict list as {})."""
    empty = {name: ({} if isinstance(getattr(REG, name), dict) else ()) for name in LISTS}
    with mock.patch.multiple(REG, **empty):
        REG.reset()
        try:
            yield
        finally:
            REG.reset()


def said(fn, *a, **kw) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


def outside_code(html: str) -> str:
    return re.sub(r"<script>.*?</script>", "", re.sub(r"<style>.*?</style>", "", html, flags=re.S), flags=re.S)


TABS = re.compile(r'<nav class="tabs" aria-label="Pages">.*?</nav>', re.S)


def outside_tabs(html: str) -> str:
    """The page without its tab strip: a built social tab (Inbox, Cards, Reports, Log) is there with every switch
    off by design, and is pinned on its own (tabs_built)."""
    return TABS.sub("", html)


def tabs_built(current: str, acct: str = "all") -> str:
    """The exact tab strip: Clips, Stats, then each social tab whose part is built here, in the plug board's order."""
    q = "" if acct == "all" else "?acct=" + acct
    top = REG.top_of(current)
    pages = list(REG.tab_pages())
    pages.insert(1, ("calendar", "/calendar", "Calendar"))       # the Calendar tab (look.py, 10-29)
    pages.insert(3, ("money", "/money", "Money"))                # the Money tab (moneypage.py, 10-29)
    return ('<nav class="tabs" aria-label="Pages">' + "".join(
        f'<a href="{url}{q}"{" aria-current=page" if key == top else ""}>{words}</a>'
        for key, url, words in pages) + "</nav>")


class OffMeansOffTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        self.clock = Clock(self.cfg.slot_time(DAY, "06:40")).patch(self, self.cfg)

    def all_off(self) -> None:
        for a in self.cfg.accounts.values():
            self.assertEqual(SW._on_words(a), "every social part is off")

    def fill(self) -> None:
        slot_ready(self, self.gta, DAY, "07:00", "10:30")
        slot_posted(self, self.gta, DAY - timedelta(days=1), "08:45")
        self.all_off()

    # ---------------------------------------------------------------------------------------------- the pages
    def test_1_live_and_stats_data_are_as_before(self):
        self.fill()
        now = self.clock()
        week = any(p.key == "week" for p in REG.children("clips"))
        for acct in ["all"] + list(self.cfg.accounts):
            for day in (DAY, DAY - timedelta(days=1)):
                got = R.live(self.cfg, day, now, acct)
                with bare():
                    before = R.live(self.cfg, day, now, acct)
                # the one difference a built part makes with every switch off: the calendar's Week link (a page
                # link, like a tab); the stamp follows the calendar, so it is compared after the link is taken out
                self.assertEqual(got["cal"].pop("week", None), True if week else None)
                got["stamp"] = R._stamp(got["sections"], got["cal"], got["needs"], got["casts"],
                                        got["kpis"] + got["breaking"] + got["rail"])
                self.assertEqual(json.dumps(got, sort_keys=True), json.dumps(before, sort_keys=True),
                                 f"/live {acct} {day}")
                self.assertNotIn("brief", got)
                self.assertNotIn("items", got["cal"])
        with_social = json.dumps(SP.data(self.cfg, now), sort_keys=True)
        with bare():
            self.assertEqual(json.dumps(SP.data(self.cfg, now), sort_keys=True), with_social)

    def test_2_the_clips_page_is_as_before_outside_its_code(self):
        self.fill()
        now = self.clock()
        for acct in ["all", "gaming"]:
            with_social = R.page(self.cfg, DAY, now, tok="t0k", acct=acct)
            with bare():
                before = R.page(self.cfg, DAY, now, tok="t0k", acct=acct)
            # the body is what it was; only the tab strip carries the built social tabs (pinned exactly below)
            self.assertEqual(outside_tabs(outside_code(with_social)), outside_tabs(outside_code(before)))
            # Lab's tab (lab.py, not the social layer) follows the social tabs when a lab account runs here
            self.assertIn(tabs_built("clips", acct), with_social.replace('<a href="/lab">Lab</a></nav>', '</nav>'))
            self.assertEqual(len(TABS.findall(with_social)), 1)
            self.assertIn('id="needs"', with_social)
            self.assertNotIn('id="brief"', with_social)
            # the only script a part adds is its own CARD_JS, and none ships today
            self.assertEqual(REG.card_js(), "")
        for current in ("clips", "stats"):
            with bare():
                before = R.tabs(current, "all")
            self.assertEqual(before, '<nav class="tabs" aria-label="Pages"><a href="/"'
                             + (' aria-current=page' if current == "clips" else "") + '>Clips</a>'
                             '<a href="/calendar">Calendar</a><a href="/stats"'
                             + (' aria-current=page' if current == "stats" else "") + '>Stats</a>'
                             '<a href="/money">Money</a></nav>')
            self.assertEqual(R.tabs(current, "all"), tabs_built(current))

    def test_3_the_morning_notice_is_as_before(self):
        self.fill()
        ns = argparse.Namespace(account=None, date=DAY.isoformat(), notify=False)
        with_social = said(F.cmd_morning, self.cfg, ns)
        with bare():
            self.assertEqual(said(F.cmd_morning, self.cfg, ns), with_social)
        self.assertEqual(SOC.morning_lines(self.cfg, self.clock()), [])

    # ---------------------------------------------------------------------------------------------- the factory
    def test_4_posting_makes_no_extra_call_and_writes_no_social_file(self):
        g, yt = FakeGraph(), FakeYouTube()             # no routes: any call to a platform fails the test
        patch_http(self, graph=g, yt=yt)
        tf = O.yt_token_file(self.gta)
        tf.parent.mkdir(parents=True, exist_ok=True)
        tf.write_text("{}")
        runs = {}
        for day, empty in ((DAY, False), (DAY + timedelta(days=1), True)):
            slot_ready(self, self.gta, day, "07:00")
            with B.edit(self.cfg, self.gta, day) as bd:
                s = bd["slots"]["07:00"]
                s["signoff"] = {"by": "Andrew", "at": B.utcnow(), "hash": B.content_hash(s)}
                s["status"] = B.SCHEDULED
            calls = []

            def poster(plat):
                def post(acct, path, words, slot):
                    calls.append((plat, slot["slot"]))
                    return {"post_id": f"{plat}-1", "url": f"https://x/{plat}"}
                return post

            def uploader(acct, slot):
                calls.append(("youtube_shorts", slot["slot"]))
                return "vid1"
            posters = {p: poster(p) for p in ("facebook_reels", "instagram_reels")}
            ctx = bare() if empty else contextlib.nullcontext()
            with ctx:
                for t in ("05:30", "07:01", "07:11", "07:21"):
                    P.post_due(self.cfg, now=self.cfg.slot_time(day, t), posters=posters, uploader=uploader)
            outs = B.read(self.gta, day)["slots"]["07:00"]["outlets"]
            runs[empty] = (sorted(calls), {k: sorted(v) for k, v in outs.items()})
        self.assertEqual(runs[False], runs[True])
        self.assertEqual(sorted(runs[False][0]), [("facebook_reels", "07:00"), ("instagram_reels", "07:00"),
                                                  ("youtube_shorts", "07:00")])
        self.assertEqual((g.calls, yt.calls), ([], []))
        for a in self.cfg.accounts.values():
            for part in ("outward", "extras", "engage", "social", "learn", "cards", "coach"):
                self.assertFalse((C.STATE / a.slug / part).exists(), f"{a.slug}/{part}")
        self.assertFalse((C.STATE / "logs" / "social-errors.log").exists())

    def test_5_collect_writes_no_extras(self):
        slots = slot_ready(self, self.gta, DAY, "07:00", "08:45")
        for s in slots.values():
            self.assertNotIn("extras", s)
            self.assertEqual(s["status"], B.READY)
        self.assertFalse((C.STATE / "gaming" / "extras").exists())
        with bare():
            again = slot_ready(self, self.gta, DAY + timedelta(days=1), "07:00")["07:00"]
        self.assertEqual(sorted(again), sorted(slots["07:00"]))

    def test_6_the_doctor_says_only_dashes(self):
        self.fill()
        with mock.patch.object(TM, "status", return_value=[]), mock.patch.object(H, "status", return_value=[]), \
                mock.patch.object(H, "drift", return_value=[]):
            out = said(F.cmd_doctor, self.cfg, argparse.Namespace(fix_paths=False))
        head = "the social layer (each part starts off; each line says what it waits for)"
        section = out.split(head)[1].split("posting runner (launchd: factory.py timer)")[0]
        lines = [ln for ln in section.splitlines() if ln.strip()]
        self.assertTrue(lines)
        for ln in lines:
            self.assertTrue(ln.startswith("  --  "), ln)
        self.assertIn("  --  Gaming: every social part is off", lines)
        self.assertIn("  --  Politics: every social part is off", lines)

    def test_6b_every_switch_has_a_line_saying_what_it_waits_for(self):
        # 2026-10-20: ten switches (guard, engage.messages, replies, cards.post, cards.story_repost, checkpoints.extra,
        # checkpoints.yt_analytics, learn.experiments, poll, trial_reels) had no doctor line while off. Each owner
        # account's off lines now name every switch: by its key, by its words (WORDS), or in a line for every
        # account ("factory.py social on <account> coach").
        self.fill()
        rows = SOC.doctor(self.cfg, self.clock())
        self.assertTrue(all(r[0] is None for r in rows), rows)
        words = {"first_comment.facebook_reels": "first comments on Facebook",
                 "first_comment.instagram_reels": "first comments on Instagram",
                 "first_comment.youtube_shorts": "first comments on YouTube",
                 "learn.playbook": "playbook: off"}
        for a in (self.gta, self.politics):
            mine = [r[1] for r in rows if r[1].startswith(a.name + " ")]
            shared = [r[1] for r in rows if "<account>" in r[1]]
            for key in SW.MODES:
                named = re.compile(r"(?<![\w.])" + re.escape(key) + r"(?![\w.])")
                said_ = any(named.search(t) or (key in words and words[key] in t) for t in mine) or any(
                    f"<account> {key}" in t for t in shared)
                self.assertTrue(said_, f"{a.name}: no doctor line says what {key} waits for")
        guard = f"{self.gta.name} guard: off (watch it first: factory.py social set gaming guard watch; "
        self.assertTrue(any(t.startswith(guard) for _, t, _ in rows), rows)

    def test_7_every_part_answers_nothing(self):
        self.fill()
        now = self.clock()
        accts = list(self.cfg.accounts.values())
        self.assertEqual(OW.health(self.cfg, now, accts), ("", ""))
        self.assertEqual((OW.summary(self.cfg, now, accts), OW.morning_lines(self.cfg, now)), ([], []))
        self.assertEqual(OW.doctor(self.cfg, now), [])
        self.assertEqual(OW.drain_job(self.cfg, now=now), [])
        for a in accts:
            for outlet in ("facebook_reels", "instagram_reels", "youtube_shorts"):
                self.assertEqual(OW.post_gate(self.cfg, a, outlet, now), "")
                self.assertEqual(OW.read_paused(self.cfg, a, outlet, now), "")
        OW.note_usage(self.cfg, self.gta, "facebook", {"buc_pct": 99.0, "regain_min": 30.0}, now)
        self.assertFalse((C.STATE / "gaming" / "outward").exists())
        self.assertEqual(SOC.card_parts(self.cfg, self.gta, DAY, {"slot": "07:00"}, now), "")
        self.assertEqual(SOC.health_lines(self.cfg, now, accts), [])
        self.assertEqual((SOC.brief(self.cfg, now), SOC.cal_items(self.cfg, now, accts)), ([], {}))
        self.assertEqual(SOC.run_job(self.cfg, "tick", quiet=True, now=now), [])
        tick = json.loads((C.STATE / "social" / "tick.json").read_text())
        self.assertEqual(tick["tick"]["at"], now.isoformat(timespec="seconds"))     # the timer ran, said nothing


class OnlyGraphReachesOutTest(unittest.TestCase):
    """Only graph.py (through direct._http and net.guard) reaches a platform: no other social module opens a URL,
    calls direct._http itself or imports a missing part at its top."""

    SEAM = ("registry", "store", "switches", "verdicts", "capabilities", "outward", "extras", "socialstore", "social",
            "socialui")

    def test_1_a_grep_finds_no_network_outside_graph(self):
        code = Path(__file__).resolve().parent.parent / "clipfactory"
        for name in self.SEAM:
            text = (code / f"{name}.py").read_text()
            for word in ("urlopen", "urllib.request", "http.client", "create_connection", "_http("):
                self.assertNotIn(word, text, f"{name}.py: {word}")
        self.assertIn("D._http(", (code / "graph.py").read_text())

    def test_2_no_seam_module_imports_a_part_at_its_top(self):
        code = Path(__file__).resolve().parent.parent / "clipfactory"
        parts = {p.module for p in REG.PAGES} | {s.partition(":")[0] for s in REG.CARD_PARTS + REG.HEALTH
                                                  + REG.SUMMARIES + REG.MORNING + REG.AFTER_TAKE + REG.FOLLOW_UPS
                                                  + REG.AFTER_POLL + REG.CAL_ITEMS}
        parts |= set(REG.EXECUTORS.values()) | set(REG.COMMANDS.values()) | {"socialmove", "weekpage"}
        parts -= set(self.SEAM)
        for name in self.SEAM + ("review", "poster", "collect", "signoff", "board", "direct", "outlets"):
            for line in (code / f"{name}.py").read_text().splitlines():
                if not line.startswith(("from ", "import ")):
                    continue
                for part in parts:
                    self.assertFalse(re.search(rf"\b(import|from \.) {part}\b", line) or
                                     re.search(rf"^from \.{part} import\b", line), f"{name}.py: {line}")


if __name__ == "__main__":
    unittest.main()
