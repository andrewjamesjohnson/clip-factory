import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The social layer's front (social.py), 2026-10-19: the card parts, health lines, Today band, calendar items and
# morning lines gathered from each part (stand-in parts here), nothing at all while every part is off, the timers'
# jobs each under its own lock, the collect and post hooks, Stop everything, `factory.py social ...` and each part's
# command, the doctor's social section, the morning notice's social lines after the accounts', decline --reasons,
# connect --with, the five launchd agents. Throwaway folders; no network.

import argparse
import contextlib
import hashlib
import importlib.abc
import importlib.machinery
import io
import json
import shutil
import subprocess
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import accounts as AC      # noqa: E402
from clipfactory import board as B          # noqa: E402
from clipfactory import collect as K        # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import hermes as H         # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import review as R         # noqa: E402
from clipfactory import signoff as S        # noqa: E402
from clipfactory import social as SOC       # noqa: E402
from clipfactory import store               # noqa: E402
from clipfactory import switches as SW      # noqa: E402
from clipfactory import timer as TM         # noqa: E402
import factory as F                         # noqa: E402

DAY = date(2031, 7, 8)


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class _Broken(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Finds clipfactory.engage, then fails to import it: a part with a bug in it."""

    def find_spec(self, name, path=None, target=None):
        if name == "clipfactory.engage":
            return importlib.machinery.ModuleSpec(name, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise RuntimeError("a bug in the inbox")


def part(name: str, **fns) -> types.ModuleType:
    mod = types.ModuleType("clipfactory." + name)
    for k, v in fns.items():
        setattr(mod, k, v)
    return mod


def said(fn, *a, **kw) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


class SocialTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-cli-"))
        cls.master = cls.tmp / "master.mp4"
        clip(cls.master, 2.0)

    def setUp(self):
        self.old_state, C.STATE = C.STATE, self.tmp / "state"
        self.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = self.tmp / "keys"
        for d in ("state", "drafts", "delivered", "keys", "accounts"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        raw["review_port"] = 0
        for a in raw["accounts"].values():
            a["delivered"] = [str(self.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["account_dir"] = str(self.tmp / "accounts" / a["name"])
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        self.cfg_path = self.tmp / "accounts.json"
        self.cfg_path.write_text(json.dumps(raw))
        self.cfg = C.load(self.cfg_path)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        REG.reset()
        self.addCleanup(REG.reset)

    def tearDown(self):
        C.STATE = self.old_state
        O.TOKENS, D.TOKENS = self.old_tokens

    def inject(self, name: str, mod) -> None:
        key = "clipfactory." + name
        if key in sys.modules:
            self.addCleanup(sys.modules.__setitem__, key, sys.modules[key])
        else:
            self.addCleanup(sys.modules.pop, key, None)
        sys.modules[key] = mod
        REG.reset()

    def ready(self, *slots: str) -> None:
        root = self.gta.delivered_dirs(DAY)[0]
        for k in slots:
            d = root / f"{k.replace(':', '')} clip-{k.replace(':', '')}"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.master, d / "01-x.mp4")
            for p in ("youtube", "tiktok", "facebook", "reels"):
                (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
        K.collect(self.cfg, self.gta, DAY)

    def main(self, *argv: str) -> str:
        with mock.patch.object(C, "load", lambda *a: self.cfg):
            return said(F.main, list(argv))

    def client(self) -> C.Account:
        return C.Account(**{**self.gta.__dict__, "slug": "a-client", "name": "A Client",
                            "raw": {**self.gta.raw, "owner": "client"}})

    # ---------------------------------------------------------------------------------------------- gathered parts
    def test_1_every_part_gathered_in_order(self):
        now = self.cfg.slot_time(DAY, "06:00")
        self.inject("coach", part("coach", card_part=lambda cfg, acct, day, s, now: f'<details data-k="cx">{s["slot"]}'
                                                                                      "</details>"))
        self.inject("firstcomment", part("firstcomment",
                                         card_part=lambda cfg, acct, day, s, now: '<details data-k="fc"></details>'))
        self.inject("outward", part(
            "outward", health=lambda cfg, now, accts: ("bad", "Instagram comments wait: Meta asked us to stop."),
            summary=lambda cfg, now, accts: [{"key": "stop", "words": "Stopped", "href": "/log", "level": "bad",
                                              "order": 0}],
            morning_lines=lambda cfg, now: ["Instagram comments on Gaming wait until 14:02: Meta asked us to stop."]))
        self.inject("engage", part(
            "engage", health=lambda cfg, now, accts: ("calm", f"Inbox: 6 to answer ({len(accts)} accounts)."),
            summary=lambda cfg, now, accts: [{"key": "inbox", "words": "Inbox: 6 to answer", "href": "/inbox",
                                              "level": "loud", "order": 3}, {"words": ""}, "junk"],
            morning_line=lambda cfg, now: "6 comments to answer"))
        self.inject("cards", part(
            "cards", morning_line=lambda cfg, now: "2 cards to sign.",
            summary=lambda cfg, now, accts: [{"key": "cards", "words": "Cards: 2 to sign", "href": "/cards",
                                              "order": 2}],
            cal_items=lambda cfg, now, accts: {"2031-07-08": {"gaming": [{"k": "card", "s": "scheduled"},
                                                                            {"k": "reel", "s": "posted"}]}}))
        s = {"slot": "07:00"}
        self.assertEqual(SOC.card_parts(self.cfg, self.gta, DAY, s, now),
                         '<details data-k="cx">07:00</details><details data-k="fc"></details>')
        self.assertEqual(SOC.card_parts(self.cfg, self.client(), DAY, s, now), "")
        self.assertEqual(SOC.health_lines(self.cfg, now, [self.gta, self.politics, self.client()]),
                         [("bad", "Instagram comments wait: Meta asked us to stop."),
                          ("calm", "Inbox: 6 to answer (2 accounts).")])
        self.assertEqual([(c["key"], c["level"], c["order"]) for c in SOC.brief(self.cfg, now, "all")],
                         [("stop", "bad", 0), ("cards", "calm", 2), ("inbox", "calm", 3)])
        self.assertEqual(SOC.cal_items(self.cfg, now, [self.gta]),
                         {"2031-07-08": {"gaming": [{"k": "card", "s": "scheduled"}]}})
        self.assertEqual(SOC.morning_lines(self.cfg, now),
                         ["Instagram comments on Gaming wait until 14:02: Meta asked us to stop.",
                          "Also: 6 comments to answer; 2 cards to sign."])

    def test_2_a_part_that_raises_is_left_out_and_logged(self):
        now = self.cfg.slot_time(DAY, "06:00")

        def boom(*a):
            raise RuntimeError("no")
        self.inject("engage", part("engage", health=boom, summary=boom, morning_line=boom))
        self.inject("cards", part("cards", morning_line=lambda cfg, now: "2 cards to sign"))
        self.assertEqual(SOC.health_lines(self.cfg, now, [self.gta]), [])
        self.assertEqual(SOC.brief(self.cfg, now), [])
        self.assertEqual(SOC.morning_lines(self.cfg, now), ["Also: 2 cards to sign."])
        self.assertIn("engage:health: RuntimeError: no", (C.STATE / "logs" / "social-errors.log").read_text())

    def test_2b_a_part_that_refuses_is_left_out_the_same_way(self):
        """A refusal (SystemExit, the house style: outward.queue on a key already queued, store._inside) is one part's
        problem too: the page's lists, the morning lines, the post hook and the doctor all go on without it."""
        now = self.cfg.slot_time(DAY, "06:00")

        def refuse(*a, **k):
            raise SystemExit("Gaming: fc:facebook:9 is already queued: it is never sent twice")
        self.inject("engage", part("engage", health=refuse, summary=refuse, morning_line=refuse, doctor=refuse))
        self.inject("coach", part("coach", card_part=refuse))
        self.inject("cards", part("cards", morning_line=lambda cfg, now: "2 cards to sign", cal_items=refuse,
                                  on_posted=lambda *a: "story queued"))
        self.inject("firstcomment", part("firstcomment", on_posted=refuse))
        self.inject("outward", part("outward", note_usage=refuse))
        self.assertEqual(SOC.health_lines(self.cfg, now, [self.gta]), [])
        self.assertEqual(SOC.brief(self.cfg, now), [])
        self.assertEqual(SOC.card_parts(self.cfg, self.gta, DAY, {"slot": "07:00"}, now), "")
        self.assertEqual(SOC.cal_items(self.cfg, now, [self.gta]), {})
        self.assertEqual(SOC.morning_lines(self.cfg, now), ["Also: 2 cards to sign."])
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["outlets"] = {"facebook_reels": {"post_id": "9"}}
        self.assertEqual(SOC.on_posted(self.cfg, self.gta, DAY, "07:00", "facebook_reels", {"post_id": "9"}, now),
                         ["story queued"])
        rows = SOC.doctor(self.cfg, now)
        self.assertIn((False, "engage: its check failed (SystemExit: Gaming: fc:facebook:9 is already queued: it "
                              "is never sent twice)", "look at clipfactory/engage.py"), rows)
        log = (C.STATE / "logs" / "social-errors.log").read_text()
        self.assertIn("firstcomment:on_posted: SystemExit: Gaming: fc:facebook:9 is already queued", log)
        self.assertIn("outward:note_usage: SystemExit", log)

    def test_3_with_every_part_off_the_clips_page_is_as_before(self):
        self.ready("07:00")
        now = self.cfg.slot_time(DAY, "06:00")
        self.assertEqual(SOC.card_parts(self.cfg, self.gta, DAY, {"slot": "07:00"}, now), "")
        self.assertEqual(SOC.health_lines(self.cfg, now, [self.gta, self.politics]), [])
        self.assertEqual((SOC.brief(self.cfg, now), SOC.cal_items(self.cfg, now, [self.gta])), ([], {}))
        self.assertEqual(SOC.morning_lines(self.cfg, now), [])
        with_social = R.live(self.cfg, DAY, now)
        self.assertNotIn("brief", with_social)
        self.assertNotIn("items", with_social["cal"])
        # the Week link is there exactly when the Week page is built (a page link, like a tab; not a social part)
        self.assertEqual("week" in with_social["cal"], any(p.key == "week" for p in REG.children("clips")))
        for name in ("social", "verdicts"):
            self.inject(name, None)
        self.assertEqual(R.live(self.cfg, DAY, now), with_social)

    # ---------------------------------------------------------------------------------------------- hooks
    def test_4_after_take_writes_extras_only_when_a_part_adds_them(self):
        wrote = []
        self.inject("extras", part("extras", write_locked=lambda *a: wrote.append(a)))
        slot = {"slot": "07:00", "publish_at": "2031-07-08T07:00:00-06:00", "folder": str(self.tmp / "d")}
        SOC.after_take(self.cfg, self.gta, slot, self.tmp / "src")
        self.assertEqual(wrote, [])                                         # every part off: nothing
        seen = []

        def on_collect(ctx):
            seen.append((ctx.acct.slug, ctx.day, ctx.folder, ctx.dest))
            ctx.extras["first_comment"] = {"facebook_reels": {"text": "Full video", "from": "default"}}
        self.inject("firstcomment", part("firstcomment", on_collect=on_collect))
        SOC.after_take(self.cfg, self.gta, slot, self.tmp / "src")
        self.assertEqual(seen, [("gaming", DAY, self.tmp / "src", self.tmp / "d")])
        self.assertEqual(wrote, [(self.gta, DAY, slot, {"first_comment": {"facebook_reels": {
            "text": "Full video", "from": "default"}}}, "collect", "defaults")])
        SOC.after_take(self.cfg, self.client(), slot, None)
        self.assertEqual(len(seen), 1)

    def test_5_on_posted_reads_the_slot_fresh_and_notes_usage(self):
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["outlets"] = {"facebook_reels": {"post_id": "9"}}
        got = []
        self.inject("firstcomment", part("firstcomment", on_posted=lambda cfg, acct, day, slot, outlet, rec, now:
                                         got.append((slot["outlets"], outlet, rec)) or "first comment queued"))
        self.inject("outward", part("outward", note_usage=lambda cfg, acct, plat, usage, now: f"{plat} {usage}"))
        now = self.cfg.slot_time(DAY, "07:01")
        with mock.patch.object(D, "last_usage", lambda: {"buc_pct": 12}, create=True):
            out = SOC.on_posted(self.cfg, self.gta, DAY, "07:00", "facebook_reels", {"post_id": "9"}, now)
        self.assertEqual(out, ["first comment queued", "facebook {'buc_pct': 12}"])
        self.assertEqual(got, [({"facebook_reels": {"post_id": "9"}}, "facebook_reels", {"post_id": "9"})])
        self.assertEqual(SOC.on_posted(self.cfg, self.gta, DAY, "09:99", "facebook_reels", {}, now), [])
        self.assertEqual(SOC.on_posted(self.cfg, self.client(), DAY, "07:00", "facebook_reels", {}, now), [])

    def test_6_a_signed_slot_whose_extras_went_away_is_red(self):
        f = C.STATE / "gaming" / "extras" / DAY.isoformat() / "0700.json"
        f.parent.mkdir(parents=True)
        f.write_text('{"v": 1}')
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        with B.edit(self.cfg, self.gta, DAY) as bd:
            for k in ("07:00", "08:45"):
                bd["slots"][k].update(status=B.SCHEDULED, extras={"path": str(f), "sha256": sha})
        before = self.cfg.slot_time(DAY, "06:00")
        self.assertEqual(SOC.extras_health(self.cfg, before, [self.gta]), ("", ""))
        f.write_text('{"v": 1, "first_comment": {}}')
        level, words = SOC.extras_health(self.cfg, before, [self.gta])
        self.assertEqual(level, "bad")
        self.assertIn(f"Gaming {DAY} 07:00: its first comment or poll was changed after it was signed", words)
        self.assertTrue(words.endswith("It will not post: Pull it, then approve it again."))
        self.assertEqual(SOC.extras_health(self.cfg, self.cfg.slot_time(DAY, "09:00"), [self.gta]), ("", ""))
        f.unlink()
        with mock.patch.object(self.cfg, "now", lambda: before):
            rows = SOC.doctor(self.cfg)
        self.assertIn((False, f"Gaming {DAY} 07:00: its first comment or poll file is gone; it will not post",
                       f"factory.py pull gaming --date {DAY} --slots 07:00 --by NAME, then approve it again on "
                       "the review page"), rows)
        self.assertIn(("bad", f"Gaming {DAY} 07:00: its first comment or poll file is gone; Gaming {DAY} 08:45: "
                              "its first comment or poll file is gone. It will not post: Pull it, then approve it "
                              "again."), SOC.health_lines(self.cfg, before, [self.gta]))

    # ---------------------------------------------------------------------------------------------- jobs and clicks
    def test_7_jobs_each_under_their_own_lock(self):
        ran = []

        def drain_job(cfg, now=None, quiet=True):
            ran.append("drain")
            return ["sent 1 first comment"]

        def tick(cfg, now=None, quiet=True):
            raise RuntimeError("inbox down")
        self.inject("outward", part("outward", drain_job=drain_job, prune_job=lambda cfg, now=None, quiet=True: []))
        self.inject("engage", part("engage", tick=tick))
        now = self.cfg.slot_time(DAY, "10:00")
        with store.try_lock("outward-prune_job") as got:
            self.assertTrue(got)
            lines = SOC.run_job(self.cfg, "tick", quiet=True, now=now)
        self.assertEqual(ran, ["drain", "drain"])
        self.assertEqual(lines, ["sent 1 first comment", "engage:tick: RuntimeError: inbox down",
                                 "sent 1 first comment"])
        tick_json = json.loads((C.STATE / "social" / "tick.json").read_text())
        fc = "ok" if REG.present("firstcomment") else "not built yet"           # built since the first-comment merge
        self.assertEqual(tick_json["tick"]["steps"],
                         [["outward:drain_job", "ok"], ["capabilities:refresh_due", "ok"],      # built since commit 3
                          ["engage:tick", "error: RuntimeError: inbox down"], ["firstcomment:sweep", fc],
                          ["outward:drain_job", "ok"], ["outward:prune_job", "busy"]])
        with self.assertRaises(SystemExit):
            SOC.run_job(self.cfg, "nothing")
        self.assertEqual(self.main("social", "studio", "--quiet"), "")       # every part off: nothing said
        self.inject("cards", None)                                           # a part not built says so
        self.assertIn("cards:tick: not built yet", self.main("social", "studio"))
        self.assertEqual(SOC.kick(self.cfg), "sending not started (offline)")

    def test_8_stop_and_start_are_page_clicks_through_outward(self):
        self.inject("outward", None)                 # built since the seam's fourth commit: hidden here, as not built
        with self.assertRaises(SystemExit) as ex:
            SOC.act(self.cfg, {"op": "social-stop"}, page=True)
        self.assertIn("not built yet", str(ex.exception))
        said_ = []
        self.inject("outward", part("outward", stop=lambda cfg, by, why="": said_.append(("stop", by, why)) or
                                    "Stopped by Andrew.", start=lambda cfg, by: "Started again."))
        with self.assertRaises(SystemExit):
            SOC.act(self.cfg, {"op": "social-stop"}, page=False)
        self.assertEqual(SOC.act(self.cfg, {"op": "social-stop", "why": "a test"}, page=True), (True, "Stopped by Andrew."))
        self.assertEqual(said_, [("stop", "Andrew", "a test")])
        self.assertEqual(SOC.act(self.cfg, {"op": "social-start"}, page=True), (True, "Started again."))
        self.assertIs(REG.act_module("social-stop"), SOC)
        with self.assertRaises(SystemExit):
            SOC.act(self.cfg, {"op": "social-dance"}, page=True)

    # ---------------------------------------------------------------------------------------------- the command
    def test_9_factory_social_commands(self):
        out = self.main("social", "status")
        self.assertIn("Stop everything: off", out)
        self.assertIn("Gaming (gaming)\n  switches: every social part is off", out)
        self.assertIn("warm-up: no creation date yet (factory.py social created gaming YYYY-MM-DD)", out)
        self.assertIn("job tick: has not run here", out)
        built = out.split("parts built: ", 1)[1].splitlines()[0].split(", ")
        for name in ("engagepage", "engage", "outward", "social"):          # every part is built since the merges
            self.assertIn(name, built)
        self.assertIn("is now practice", self.main("social", "on", "gaming", "first_comment.youtube_shorts",
                                                   "--practice"))
        self.assertIn("coach is now on", self.main("social", "on", "gaming", "coach", "--by", "Andrew"))
        self.assertIn("guard is now watch", self.main("social", "set", "gaming", "guard", "watch"))
        self.assertIn("warmup_days is now 21", self.main("social", "set", "gaming", "warmup_days", "21"))
        self.assertIn("created is now", self.main("social", "created", "gaming", "2026-09-01"))
        self.assertIn("is a tester", self.main("social", "tester", "gaming", "--platform", "instagram", "--id",
                                               "1784", "--name", "Andrew"))
        self.assertIn("coach is now off", self.main("social", "off", "gaming", "coach"))
        with self.assertRaises(SystemExit):
            self.main("social", "on", "gaming", "automations")             # the checklist is open
        self.assertEqual(SW.modes(self.gta)["guard"], "watch")
        self.assertIn("switches: on: guard watch, first_comment.youtube_shorts practice", self.main("social", "status",
                                                                                                    "gaming"))
        holds = C.STATE / "gaming" / "outward" / "holds.json"
        holds.parent.mkdir(parents=True)
        holds.write_text(json.dumps({"holds": {
            "instagram|comment": {"until": "2099-01-01T00:00:00+00:00", "why": "Meta asked us to stop (368)",
                                  "releasable": False},
            "facebook|read": {"until": "2000-01-01T00:00:00+00:00", "why": "over"}}}))
        out = self.main("social", "holds", "gaming")
        self.assertIn("Gaming: 1 hold\n  instagram|comment until 2098-12-31 17:00: Meta asked us to stop (368) "
                      "(cannot be released early)", out)
        self.assertEqual(self.main("social", "send", "--quiet"), "")
        self.assertEqual(self.main("social", "send"), "")                   # outward.py: nothing is queued
        self.assertEqual(self.main("social", "tick", "--quiet"), "")
        self.assertNotIn("not built yet", self.main("engage", "status"))     # the part's own command, built
        self.inject("engage", None)
        self.assertEqual(self.main("engage", "status"), "engage: not built yet\n")
        self.assertEqual(self.main("learn", "daily", "--quiet"), "")
        self.assertIn("Stopped by Andrew", self.main("social", "stop", "--why", "a test"))
        self.assertIn("Stop everything: Stop everything is on (by Andrew at ", self.main("social", "status"))
        self.assertIn("Started again by Andrew", self.main("social", "start"))
        self.assertIn("Stop everything: off", self.main("social", "status"))
        with self.assertRaises(SystemExit) as ex:                            # socialmove.py is built: its own check
            self.main("social", "export", "gaming", "--to", "x")
        self.assertIn("full path", str(ex.exception))
        for argv in (("import", "--from", str(self.tmp / "nowhere")), ("import", str(self.tmp / "nowhere"))):
            with self.assertRaises(SystemExit) as ex:                        # "--from" reaches socialmove whole
                self.main("social", *argv)
            self.assertIn("no such folder", str(ex.exception))
        self.inject("socialmove", None)
        with self.assertRaises(SystemExit) as ex:
            self.main("social", "export", "gaming", "--to", "x")
        self.assertIn("not built yet", str(ex.exception))
        out = self.main("social", "probe", "gaming", "scopes")      # capabilities.py: no sign-in here, no network
        self.assertIn("Gaming Meta: not connected", out)
        self.assertIn("Gaming YouTube: not connected", out)
        self.assertIn("--  Gaming: on: guard watch", self.main("social", "doctor"))

    def test_10_doctor_has_a_social_section(self):
        def doctor() -> str:
            with mock.patch.object(TM, "status", return_value=[]), mock.patch.object(H, "status", return_value=[]), \
                    mock.patch.object(H, "drift", return_value=[]):
                return said(F.cmd_doctor, self.cfg, argparse.Namespace(fix_paths=False))
        out = doctor()
        head = "the social layer (each part starts off; each line says what it waits for)"
        self.assertIn(head, out)
        section = out.split(head)[1].split("posting runner (launchd: factory.py timer)")[0]
        self.assertIn("\n  --  Gaming: every social part is off\n", section)
        self.assertIn("\n  --  Politics: every social part is off\n", section)
        self.assertIn("--  parts built here: ", section)
        self.assertNotIn("NO ", section)                                    # off means only "--" lines
        self.assertTrue(out.index("the bench (spare clips") < out.index(head))
        finder = _Broken()
        sys.meta_path.insert(0, finder)
        self.addCleanup(sys.meta_path.remove, finder)
        self.inject("engage", None)                     # out of sys.modules (it is built and imported by now) ...
        sys.modules.pop("clipfactory.engage")           # ... so the finder's broken copy is what loads
        REG.reset()
        out = doctor()
        self.assertIn("NO  clipfactory/engage.py will not load: RuntimeError: a bug in the inbox (its tab, card part "
                      "and command are hidden)", out)
        self.assertIn("fix: fix clipfactory/engage.py", out)
        with mock.patch.object(SOC, "doctor", side_effect=RuntimeError("boom")):
            out = doctor()
        self.assertIn("NO  the social layer: cannot check it (RuntimeError: boom)", out)
        self.assertIn("posting runner (launchd: factory.py timer)", out)    # doctor went on
        with mock.patch.object(SOC, "doctor", side_effect=SystemExit("refused")):
            out = doctor()
        self.assertIn("NO  the social layer: cannot check it (SystemExit: refused)", out)
        self.assertIn("posting runner (launchd: factory.py timer)", out)    # a refusal: doctor went on too

    def test_11_the_morning_notice_carries_social_lines_after_the_accounts(self):
        self.inject("outward", part("outward", morning_lines=lambda cfg, now: ["Instagram comments wait: Meta asked "
                                                                               "us to stop."]))
        self.inject("engage", part("engage", morning_line=lambda cfg, now: "6 comments to answer"))
        out = said(F.cmd_morning, self.cfg, argparse.Namespace(account=None, date=DAY.isoformat(), notify=False))
        lines = out.splitlines()
        self.assertNotIn(lines[0], ("Instagram comments wait: Meta asked us to stop.", "Also: 6 comments to answer."))
        self.assertLess(lines.index(f"Gaming: no clips made for {DAY} yet."),
                        lines.index("Instagram comments wait: Meta asked us to stop."))
        self.assertLess(lines.index(f"Politics: no clips made for {DAY} yet."),
                        lines.index("Instagram comments wait: Meta asked us to stop."))
        self.assertEqual(lines[lines.index("Instagram comments wait: Meta asked us to stop.") + 1],
                         "Also: 6 comments to answer.")
        with mock.patch.object(SOC, "morning_lines", side_effect=RuntimeError("boom")):
            out = said(F.cmd_morning, self.cfg, argparse.Namespace(account=None, date=DAY.isoformat(), notify=False))
        self.assertIn("Review and sign:", out)
        with mock.patch.object(SOC, "morning_lines", side_effect=SystemExit("refused")):
            out = said(F.cmd_morning, self.cfg, argparse.Namespace(account=None, date=DAY.isoformat(), notify=False))
        self.assertIn("Review and sign:", out)

    def test_12_decline_reasons_and_connect_with(self):
        got = []

        def new_decline(cfg, acct, day, slots, by, reason="", remake=False, reasons=None):
            got.append((reason, reasons))
            return []
        with mock.patch.object(S, "decline", new_decline):
            self.main("decline", "gaming", "--date", DAY.isoformat(), "--slots", "07:00", "--by", "Andrew",
                      "--reason", "too dark", "--reasons", "framing,weak_hook")
            with self.assertRaises(SystemExit):
                self.main("decline", "gaming", "--date", DAY.isoformat(), "--slots", "07:00", "--by", "Andrew",
                          "--reasons", "dull")
            self.main("decline", "gaming", "--date", DAY.isoformat(), "--slots", "07:00", "--by", "Andrew",
                      "--reasons", "framing")
        self.assertEqual(got, [("too dark", ["framing", "hook"]), ("", ["framing"])])   # weak_hook is read as hook
        self.assertEqual(F.with_arg("comments, analytics,comments"), ["comments", "analytics"])
        with self.assertRaises(SystemExit):
            F.with_arg("comments,delete")
        for argv in (("connect", "meta", "gaming", "--with", "comments"),
                     ("connect", "youtube", "gaming", "--client", "c.json", "--with", "analytics")):
            with mock.patch.object(O, "connect_youtube", lambda acct, client, cfg=None: "connected"), \
                    self.assertRaises(SystemExit):
                self.main(*argv)
        with mock.patch.object(O, "connect_youtube", lambda acct, client, cfg=None, extra=(): f"connected {extra}"):
            self.assertIn("connected ('analytics',)", self.main("connect", "youtube", "gaming", "--client", "c.json",
                                                                 "--with", "analytics"))
            self.assertIn("connected ()", self.main("connect", "youtube", "gaming", "--client", "c.json"))

    # ---------------------------------------------------------------------------------------------- the rest of the seam
    def test_13_agents_names_and_notes(self):
        social = {a["name"]: a for a in TM.AGENTS[-5:]}
        self.assertEqual(list(social), ["social", "checkpoints", "studio", "learn", "ideas"])
        self.assertEqual([social[n].get("interval") for n in ("social", "checkpoints", "studio")], [300, 900, 600])
        self.assertEqual(social["learn"]["at"], {"Hour": 4, "Minute": 40})
        self.assertEqual(social["ideas"]["at"], {"Hour": 14, "Minute": 20})
        for a in social.values():
            self.assertEqual((a["quiet"], a["twins"], a["args"][0], a["args"][-1]), (True, [], "social", "--quiet"))
            pl = TM.plist(a)
            self.assertNotIn("CLIP_FACTORY_LOG_STAMP", pl["EnvironmentVariables"])
            self.assertEqual(pl["ProcessType"], "Background")              # light: never the render queue
        self.assertIn("com.clipfactory.social    every 300 s   factory.py social tick --quiet", TM.__doc__)
        for word in ("inbox", "engage", "cards", "extras", "coach", "learn", "outward", "series", "ideas", "playbook",
                     "reports", "log", "social", "render", "prompts", "rules", "week", "verdicts", "capabilities",
                     "docs"):
            self.assertIn(word, AC.RESERVED)
        for slug in ("gaming", "politics"):
            self.assertEqual(H.TASK_PROMPTS[slug][-4:], ("clip-coach", "clip-weekly-report", "clip-results-coach-auto",
                                                  "clip-fixer"))
        how = json.loads((ROOT / "config" / "accounts.json").read_text())["_social_how"]
        self.assertTrue(how.startswith("The social layer (Clip Review's Inbox, Cards, Reports and Log tabs; "
                                       "clipfactory/social.py) ships off for every account."))
        for line in ("factory.py social tick|studio|checkpoints|learn|ideas [--quiet]", "factory.py social send",
                     "factory.py social status [account]", "factory.py social stop [--why ..] | start",
                     "factory.py social set <account> <key> <value>", "factory.py social on <account> <part>",
                     "factory.py social created <account> YYYY-MM-DD", "factory.py social tester <account>",
                     "factory.py social probe <account> <what>", "factory.py social holds [account] | release",
                     "factory.py social forget <account>", "factory.py social export <account> --to DIR | import",
                     "factory.py engage | rules | extras | cards | coach | checkpoints | learn | ideas",
                     "--with comments,analytics", "[--reasons hook,framing]"):
            self.assertIn(line, F.__doc__)
        for word in ("—", "–", "!"):
            self.assertNotIn(word, SOC.__doc__ + SW.__doc__ + how)


if __name__ == "__main__":
    unittest.main()
