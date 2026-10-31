import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The poster and the social guard (poster.py's hooks into outward.py), 2026-10-19: with the guard off a post goes
# exactly as before and nothing social is written; watch logs every post and what it would have held; on, a block or
# a Business Use Case limit holds that platform's posts (a wait, not one of the three tries) and the stats and
# followers reads skip a held platform. A social part that raises never stops a post. Fake posters, throwaway
# folders; nothing can reach the network.

import json
import shutil
import subprocess
import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B          # noqa: E402
from clipfactory import collect as K        # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import followers as FL     # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import outward as OW       # noqa: E402
from clipfactory import poster as P         # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import signoff as S        # noqa: E402
from clipfactory import social as SOC       # noqa: E402
from clipfactory import stats as ST         # noqa: E402
from clipfactory import switches as SW      # noqa: E402

DAY = date(2031, 5, 6)


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def no_network(*a, **kw):
    raise AssertionError("a platform call in a poster test")


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-poster-"))
        cls.master = cls.tmp / "master.mp4"
        clip(cls.master, 2.0)

    def setUp(self):
        for d in ("state", "drafts", "delivered", "keys"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.old = (C.STATE, ST.OUT, O.TOKENS, D.TOKENS)
        C.STATE = self.tmp / "state"
        ST.OUT = C.STATE / "stats"
        O.TOKENS = D.TOKENS = self.tmp / "keys"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        raw["review_port"] = 0
        raw.pop("inbox_auto_sign", None)
        for slug, a in raw["accounts"].items():
            if slug not in ("gaming", "politics"):   # the two this file reads (Stunts and Gaming
                a["active"] = False                           # Garage are on in the live file since 2026-10-29)
            a.pop("signed_by_hand_stays", None)
            a["delivered"] = [str(self.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["account_dir"] = str(self.tmp / "accounts" / a["name"])
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "export"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("gaming")
        self.calls = []
        self.fails = {}
        self.usage = {}                 # (platform, slot) -> what that answer's rate-limit headers said
        self.saved = []
        self.patch(D, "_http", no_network)
        D._LAST.usage = {}
        REG.reset()
        self.addCleanup(REG.reset)

    def tearDown(self):
        for obj, name, value in reversed(self.saved):
            setattr(obj, name, value)
        C.STATE, ST.OUT, O.TOKENS, D.TOKENS = self.old
        D._LAST.usage = {}

    def patch(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def inject(self, name: str, mod) -> None:
        key = "clipfactory." + name
        if key in sys.modules:
            self.addCleanup(sys.modules.__setitem__, key, sys.modules[key])
        else:
            self.addCleanup(sys.modules.pop, key, None)
        sys.modules[key] = mod
        REG.reset()

    # ------------------------------------------------------------------------------------------------- helpers
    def ready(self, *slots: str) -> None:
        root = self.acct.delivered_dirs(DAY)[0]
        for k in slots:
            d = root / f"{k.replace(':', '')} clip-{k.replace(':', '')}"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.master, d / "01-x.mp4")
            for p in ("youtube", "tiktok", "facebook", "reels"):
                (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, list(slots), "Andrew")

    def at(self, hhmm: str) -> datetime:
        return self.cfg.slot_time(DAY, hhmm)

    def posters(self):
        def make(plat):
            def post(acct, path, words, slot):
                self.calls.append((plat, slot["slot"]))
                u = self.usage.get((plat, slot["slot"]))
                if u is not None:           # as direct._http keeps an answer's headers
                    D._LAST.usage = dict(u)
                e = self.fails.get((plat, slot["slot"]))
                if e is not None:
                    raise e
                return {"post_id": f"{plat[:2]}-{slot['slot']}", "url": f"https://x/{plat}/{slot['slot']}",
                        "published_at": self.at(slot["slot"]).isoformat()}
            return post
        return {p: make(p) for p in ("facebook_reels", "instagram_reels", "tiktok")}

    def run_at(self, hhmm: str):
        return P.post_due(self.cfg, now=self.at(hhmm), posters=self.posters(), uploader=no_network)

    def rec(self, k: str, plat: str) -> dict:
        return B.read(self.acct, DAY)["slots"][k]["outlets"].get(plat) or {}

    def guard(self, value: str) -> None:
        if value == "off":
            return
        SW.set_mode(self.cfg, self.acct, "guard", value, "Andrew")

    def rows(self, **kw):
        return list(OW.rows(self.cfg, "gaming", **kw))


class GuardTest(Base):
    def test_1_off_posts_as_before_and_writes_nothing(self):
        self.ready("07:00")
        self.fails[("instagram_reels", "07:00")] = D.PlatformError("(#368) blocked", status=400, meta_code=368)
        lines = self.run_at("07:01")
        self.assertIn("  Gaming 2031-05-06 07:00 facebook_reels: posted https://x/facebook_reels/07:00", lines)
        self.assertIn("  Gaming 2031-05-06 07:00 instagram_reels: FAILED (1/3) (#368) blocked", lines)
        self.assertEqual(self.rec("07:00", "instagram_reels")["tries"], 1)       # a try, as before
        self.assertFalse((C.STATE / "gaming" / "outward").exists())
        self.assertFalse((C.STATE / "gaming" / "social").exists())
        self.assertFalse((C.STATE / "logs" / "social-errors.log").exists())

    def test_2_watch_logs_every_post_and_holds_nothing(self):
        self.guard("watch")
        self.ready("07:00")
        self.fails[("instagram_reels", "07:00")] = D.PlatformError("(#368) blocked", status=400, meta_code=368)
        # 2026-10-20 review: Meta's answer to the post that went asks for a wait; watch still holds nothing
        self.usage[("facebook_reels", "07:00")] = {"regain_min": 10.0, "buc_pct": 95.0, "host": "graph.facebook.com"}
        self.run_at("07:01")
        self.assertEqual(self.rec("07:00", "instagram_reels")["tries"], 1)       # watch changes nothing
        rows = self.rows()
        done = [r for r in rows if r["state"] == "done"]
        self.assertEqual(len(done), 1)
        self.assertEqual((done[0]["kind"], done[0]["key"], done[0]["platform"], done[0]["result"]["id"]),
                         ("post", "post:facebook_reels:2031-05-06 07:00", "facebook", "fa-07:00"))
        self.assertEqual(done[0]["target"], {"board": "2031-05-06", "slot": "07:00", "post_id": "fa-07:00"})
        failed = [r for r in rows if r["state"] == "failed"][0]
        self.assertEqual((failed["error"]["kind"], failed["error"]["code"]), ("block", 368))
        watch = [r for r in rows if r["state"] == "watch"]
        self.assertEqual(sorted(r["why"] for r in watch),
                         ["would pause everything social for 30 minutes: Meta asked us to wait 10 minutes (too many "
                          "calls)", "would pause posts and stories for 720 minutes: Meta blocked publishing (368)"])
        self.assertEqual(OW._holds("gaming"), {})
        self.assertEqual(OW.health(self.cfg, self.at("07:01"), [self.acct]), ("", ""))
        self.assertEqual(OW.counts(self.cfg, self.acct, "facebook", "post", 86400), 1)

    def test_3_on_holds_the_platform_not_a_try(self):
        self.guard("on")
        self.ready("07:00", "08:45")
        self.fails[("instagram_reels", "07:00")] = D.PlatformError("(#368) blocked", status=400, meta_code=368)
        lines = self.run_at("07:01")
        self.assertTrue(any(ln.startswith("  Gaming 2031-05-06 07:00 instagram_reels: waits, Instagram posts and "
                                          "stories paused until ") for ln in lines), lines)
        ig = self.rec("07:00", "instagram_reels")
        self.assertNotIn("tries", ig)                                            # not one of the three tries
        self.assertIn("(#368) blocked", ig["last_error"])
        self.assertIn("instagram_reels: waits, Instagram posts and stories paused",
                      B.last_note(B.read(self.acct, DAY)["slots"]["07:00"]))
        self.assertEqual(self.rec("07:00", "facebook_reels")["post_id"], "fa-07:00")
        h = OW._holds("gaming")["instagram|publish"]
        self.assertEqual((h["code"], h["releasable"], h["kind"]), (368, True, "block"))
        self.assertEqual([r["state"] for r in self.rows(kinds=("post",)) if r["platform"] == "instagram"], ["held"])
        del self.fails[("instagram_reels", "07:00")]
        self.calls.clear()
        self.run_at("07:15")
        self.assertNotIn(("instagram_reels", "07:00"), self.calls)               # the hold keeps it
        OW.release(self.cfg, self.acct, "instagram|publish", "Andrew")
        self.run_at("07:30")
        self.assertEqual(self.rec("07:00", "instagram_reels")["post_id"], "in-07:00")
        # a regain time from Meta's usage header: everything on that platform, no early release
        self.usage[("facebook_reels", "08:45")] = {"regain_min": 10.0, "host": "graph.facebook.com"}
        self.fails[("facebook_reels", "08:45")] = D.PlatformError("(#80001) too many", status=400, meta_code=80001)
        self.run_at("08:46")
        h = OW._holds("gaming")["facebook|*"]
        self.assertFalse(h["releasable"])
        self.assertNotIn("tries", self.rec("08:45", "facebook_reels"))

    def test_3b_meta_numbers_never_hold_youtube(self):
        """direct.last_usage() is the thread's last answer through direct._http; a YouTube upload does not go through
        it. A Facebook answer's regain time, still there when YouTube comes next, never pauses YouTube."""
        self.guard("on")
        D._LAST.usage = {"regain_min": 20.0, "buc_pct": 95.0, "host": "graph.facebook.com"}
        said = SOC.on_posted(self.cfg, self.acct, DAY, "07:00", "youtube_shorts", {"video_id": "v1"}, self.at("07:00"))
        self.assertEqual(said, [])
        self.assertEqual(OW.post_tripwire(self.cfg, self.acct, "youtube_shorts",
                                          RuntimeError("HTTP 503 from www.googleapis.com"), self.at("07:00")), "")
        self.assertEqual(OW._holds("gaming"), {})
        self.assertEqual(OW.post_gate(self.cfg, self.acct, "youtube_shorts", self.at("07:01")), "")
        self.assertFalse(OW.slow(self.cfg, self.acct, "youtube", self.at("07:01")))
        # and the poster forgets the last answer's numbers before each platform: an earlier post's regain time is
        # not this one's
        self.ready("08:45")
        D._LAST.usage = {"regain_min": 20.0, "host": "graph.facebook.com"}
        self.fails[("facebook_reels", "08:45")] = D.PlatformError("HTTP 400: (#100) odd", status=400, meta_code=100)
        self.run_at("08:46")
        self.assertNotIn("facebook|*", OW._holds("gaming"))
        self.assertEqual(self.rec("08:45", "facebook_reels")["tries"], 1)

    def test_4_rate_limits_stay_with_the_posters_meta_hold(self):
        self.guard("on")
        self.ready("07:00")
        self.fails[("facebook_reels", "07:00")] = D.PlatformError(
            "HTTP 400: (#4) Application request limit reached", status=400, meta_code=4)
        self.run_at("07:01")
        self.assertTrue(O.meta_hold(self.acct))
        self.assertEqual(OW._holds("gaming"), {})
        O.meta_clear(self.acct)


class HooksTest(Base):
    def test_1_a_raising_social_part_never_stops_a_post(self):
        self.guard("on")
        seen = []
        fc = types.ModuleType("clipfactory.firstcomment")
        fc.on_posted = lambda *a: 1 / 0
        cards = types.ModuleType("clipfactory.cards")
        cards.on_posted = lambda cfg, acct, day, slot, outlet, rec, now: seen.append(
            (acct.slug, day, slot["slot"], outlet, slot["outlets"][outlet].get("post_id"), rec.get("post_id")))
        self.inject("firstcomment", fc)
        self.inject("cards", cards)
        ow = REG.load("outward")                       # the module the poster reaches through the plug board
        self.patch(ow, "record_post", lambda *a, **k: 1 / 0)
        self.patch(ow, "post_gate", lambda *a, **k: 1 / 0)
        self.patch(ow, "post_tripwire", lambda *a, **k: 1 / 0)
        self.ready("07:00")
        self.fails[("instagram_reels", "07:00")] = D.PlatformError("(#368) blocked", status=400, meta_code=368)
        lines = self.run_at("07:01")
        self.assertIn("  Gaming 2031-05-06 07:00 facebook_reels: posted https://x/facebook_reels/07:00", lines)
        self.assertEqual(self.rec("07:00", "instagram_reels")["tries"], 1)       # the guard broke: a plain try
        self.assertEqual(seen, [("gaming", DAY, "07:00", "facebook_reels", "fa-07:00", "fa-07:00")])
        log = (C.STATE / "logs" / "social-errors.log").read_text()
        self.assertIn("firstcomment:on_posted: ZeroDivisionError", log)
        self.assertIn("outward:record_post: ZeroDivisionError", log)
        self.assertFalse(any(" broke " in ln for ln in lines))

    def test_1b_a_follow_up_that_refuses_never_stops_the_pass(self):
        """firstcomment:on_posted refusing (outward.queue: the key is already queued, the 5-minute sweep got there
        first) is that part's problem: every due slot still posts and the run is not counted as broken."""
        self.guard("on")

        def refuse(*a, **k):
            raise SystemExit("Gaming: fc:facebook:fa-07:00 is already queued: it is never sent twice")
        self.inject("firstcomment", types.ModuleType("clipfactory.firstcomment"))
        sys.modules["clipfactory.firstcomment"].on_posted = refuse
        ow = REG.load("outward")
        self.patch(ow, "record_post", refuse)
        self.patch(ow, "post_gate", refuse)
        self.patch(ow, "post_tripwire", refuse)
        self.patch(ow, "note_usage", refuse)
        self.patch(ow, "read_paused", refuse)
        self.assertEqual(ST.read_paused(self.cfg, self.acct, "facebook_reels"), "")     # a read is never stopped
        self.ready("07:00", "08:45")
        self.fails[("instagram_reels", "08:45")] = D.PlatformError("(#368) blocked", status=400, meta_code=368)
        lines = self.run_at("09:00")              # Facebook's hook refused first; Instagram went on in that pass
        self.assertEqual(self.rec("07:00", "facebook_reels")["post_id"], "fa-07:00")
        self.assertEqual(self.rec("07:00", "instagram_reels")["post_id"], "in-07:00")
        lines += self.run_at("09:11")             # the next slot, after the ten minutes between posts
        self.assertEqual(self.rec("08:45", "facebook_reels")["post_id"], "fa-08:45")
        self.assertEqual(self.rec("08:45", "instagram_reels")["tries"], 1)      # the guard refused: a plain try
        self.assertFalse(any(" broke " in ln for ln in lines))
        self.assertFalse(P._read_health().get("fails"))
        self.assertEqual(P.health_words(self.cfg), "")
        self.assertIn("firstcomment:on_posted: SystemExit", (C.STATE / "logs" / "social-errors.log").read_text())

    def test_2_record_post_rows(self):
        self.guard("on")
        OW.record_post(self.cfg, self.acct, DAY, "07:00", "instagram_reels", {"gave_up": True, "why": "x"}, "t")
        OW.record_post(self.cfg, self.acct, DAY, "08:45", "instagram_reels", {"tries": 1, "last_error": "e"}, "t")
        OW.record_post(self.cfg, self.acct, DAY, "10:30", "facebook_reels", {"last_error": "Meta"}, "t")
        OW.record_post(self.cfg, self.acct, DAY, "12:15", "youtube_shorts", {"video_id": "v1", "url": "u"}, "t")
        OW.record_post(self.cfg, self.acct, DAY, "14:00", "facebook_reels", {"waiting": "processing"}, "t")
        OW.record_post(self.cfg, self.acct, DAY, "14:00", "tiktok", {"post_id": "t1"}, "t")
        self.assertEqual([(r["platform"], r["state"]) for r in self.rows()],
                         [("instagram", "gave_up"), ("instagram", "failed"), ("facebook", "held"),
                          ("youtube", "done")])


class ReadsTest(Base):
    def lister(self, plat):
        def f(acct, since):
            self.calls.append(("list", plat))
            return []
        return f

    def test_1_stats_and_followers_skip_a_held_platform(self):
        listers = {p: self.lister(p) for p in ("facebook_reels", "instagram_reels")}
        readers = {"facebook_reels": lambda a: {"followers": 5}, "instagram_reels": lambda a: {"followers": 7}}
        OW.hold(self.cfg, self.acct, "facebook", "read", 3600, "Meta's limit at 95%")
        self.guard("watch")                                                       # not on: everything is read
        ST.refresh(self.cfg, listers=listers)
        self.assertIn(("list", "facebook_reels"), self.calls)
        got, _ = FL.read_all(self.cfg, readers)
        self.assertEqual(got["gaming"]["facebook_reels"]["followers"], 5)
        SW.set_mode(self.cfg, self.acct, "guard", "on", "Andrew")
        self.calls.clear()
        lines = ST.refresh(self.cfg, listers=listers)
        self.assertIn("  Gaming facebook_reels: not read now, Facebook reading paused until ", "\n".join(lines))
        self.assertEqual(self.calls, [("list", "instagram_reels"), ("list", "facebook_reels"),
                                      ("list", "instagram_reels")])             # the Politics has no hold
        got, lines = FL.read_all(self.cfg, readers)
        self.assertEqual(got["gaming"]["facebook_reels"]["followers"], 5)    # the last numbers stay
        self.assertTrue(any("facebook_reels followers: not read now" in ln for ln in lines))
        self.assertEqual(got["politics"]["facebook_reels"]["followers"], 5)
        shutil.rmtree(ST.OUT)
        got, _ = FL.read_all(self.cfg, readers)                                    # nothing kept: an error entry
        self.assertIn("not read now", got["gaming"]["facebook_reels"]["error"])


if __name__ == "__main__":
    unittest.main()
