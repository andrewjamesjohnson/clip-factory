import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# What /file may show from state/ (2026-10-19): the social layer's cards and stills live under state/, so /file serves
# them, but only a video, a picture or captions: never a JSON file, a log or the page's own review-token. The drafts
# and the cast stills are as before. Also the shared test fakes (social_fakes.py) behave as the tests rely on: fake
# Graph and YouTube answers, the fake card engine, the fake yt-dlp and the clock. Throwaway folders; no network.

import json
import subprocess
import sys
import unittest
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import (Clock, FakeEngine, FakeGraph, FakeYouTube, cfg_in_tmp, fake_ytdlp,  # noqa: E402
                          patch_http, serve, slot_posted, write_tokens)

from clipfactory import board as B          # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import graph as G          # noqa: E402
from clipfactory import review as R         # noqa: E402

DAY = date(2031, 7, 8)


def q(p: Path) -> str:
    return "/file?p=" + urllib.parse.quote(str(p))


class FileGuardTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)

    def test_1_state_shows_only_videos_pictures_and_captions(self):
        R.token()                                                       # state/review-token exists
        tok = C.STATE / "review-token"
        self.assertTrue(tok.is_file())
        card = C.STATE / "gaming" / "cards" / str(DAY) / "quote-1730"
        card.mkdir(parents=True)
        for name in ("1.jpg", "1.mp4", "2.PNG", "3.jpeg", "clip.srt", "spec.json", "notes.txt", "x.mp4.json"):
            (card / name).write_bytes(b"x" * 10)
        (C.STATE / "gaming" / "social").mkdir(parents=True)
        (C.STATE / "gaming" / "social" / "settings.json").write_text("{}")
        get, _ = serve(self, self.cfg)
        self.assertEqual(get(q(tok))[0], 404)
        for name in ("spec.json", "notes.txt", "x.mp4.json"):
            self.assertEqual(get(q(card / name))[0], 404, name)
        self.assertEqual(get(q(C.STATE / "gaming" / "social" / "settings.json"))[0], 404)
        for name in ("1.jpg", "1.mp4", "2.PNG", "3.jpeg", "clip.srt"):
            code, _, body = get(q(card / name))
            self.assertEqual((code, body), (200, b"x" * 10), name)
        self.assertFalse(R._allowed_file(self.cfg, C.STATE / "gaming" / "cards" / "missing.jpg"))
        self.assertFalse(R._allowed_file(self.cfg, card / ".." / ".." / ".." / "review-token"))

    def test_2_the_drafts_are_as_before(self):
        slots = slot_posted(self, self.cfg.account("gaming"), DAY, "07:00")
        clip = Path(slots["07:00"]["clip"]["path"])
        side = clip.parent / "notes.json"
        side.write_text("{}")
        get, _ = serve(self, self.cfg)
        self.assertEqual(get(q(clip))[0], 200)
        self.assertEqual(get(q(side))[0], 200)                         # the drafts folder is not narrowed
        self.assertEqual(get(q(self.cfg_path))[0], 404)                # outside both


class FakesTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")

    def test_1_graph_and_youtube_answer_through_direct_http(self):
        write_tokens(self.gta)
        g = FakeGraph().on("GET", r"/17890/comments$", {"data": [{"id": "1"}]},
                           headers=FakeGraph.usage(buc_pct=42, regain_min=3))
        g.on("POST", r"/comments$", FakeGraph.meta_error(368, 1390008, msg="(#368) Temporarily blocked"), times=1)
        g.on("POST", r"/comments$", lambda call: {"id": "c-" + call["form"]["message"]})
        yt = FakeYouTube().on("GET", r"^/commentThreads$", {"items": []})
        router = patch_http(self, graph=g, yt=yt)
        self.assertIs(router.graph, g)
        self.assertEqual(G.gget(self.gta, "17890/comments", {"fields": "id"}), {"data": [{"id": "1"}]})
        self.assertEqual(D.last_usage(), {"buc_pct": 42.0, "regain_min": 3.0, "host": "graph.facebook.com"})
        self.assertEqual(g.calls[0]["params"]["access_token"], "EAAfakefakefakefake")
        with self.assertRaises(D.PlatformError) as ex:
            G.gpost(self.gta, "17890/comments", form={"message": "hi"})
        info = G.classify(ex.exception)
        self.assertEqual((info.kind, info.code, info.subcode), ("block", 368, 1390008))
        self.assertEqual(G.gpost(self.gta, "17890/comments", form={"message": "hi"}), {"id": "c-hi"})
        self.assertEqual(D.last_usage(), {})
        self.assertEqual(G.yt_get(self.gta, "commentThreads", {"part": "id"}), {"items": []})
        self.assertEqual(yt.calls[0]["headers"]["Authorization"], "Bearer fake-yt-token")
        self.assertEqual(g.paths(), ["GET /17890/comments", "POST /17890/comments", "POST /17890/comments"])
        with self.assertRaises(AssertionError):
            D._http("DELETE", f"{D.GRAPH}/17890")
        with self.assertRaises(AssertionError):
            D._http("GET", f"{D.GRAPH}/nothing-here")
        self.assertEqual(g.unmatched, ["GET /nothing-here"])
        with self.assertRaises(AssertionError):
            D._http("GET", "https://open.tiktokapis.com/v2/user/info/")
        err = FakeYouTube.google_error("quotaExceeded")
        self.assertEqual((err.status, err.reason, G.classify(err).kind), (403, "quotaExceeded", "quota"))

    def test_2_engine_ytdlp_and_clock(self):
        eng = FakeEngine.script(self.tmp)
        out = self.tmp / "state" / "gaming" / "cards" / "x" / "1.jpg"
        spec = self.tmp / "spec.json"
        spec.write_text(json.dumps({"out": str(out), "outputs": [str(out.with_suffix(".mp4"))]}))
        r = subprocess.run([sys.executable, "-B", str(eng), str(spec)], capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(r.stdout)["ok"], True)
        self.assertTrue(out.read_bytes().startswith(b"\xff\xd8"))
        self.assertTrue(out.with_suffix(".mp4").is_file())
        self.assertEqual(len(FakeEngine.calls(self.tmp)), 1)
        run = fake_ytdlp({"@a": [{"id": "v1", "title": "One"}], "@b": "ERROR: Sign in to confirm you're not a bot"})
        got = run(["yt-dlp", "--flat-playlist", "--dump-single-json", "https://www.youtube.com/@a/shorts"],
                  capture_output=True, text=True)
        self.assertEqual(json.loads(got.stdout)["entries"], [{"id": "v1", "title": "One"}])
        bad = run(["/opt/homebrew/bin/yt-dlp", "-J", "https://www.youtube.com/@b/shorts"], capture_output=True)
        self.assertEqual((bad.returncode, bad.stdout), (1, b""))
        self.assertIn(b"not a bot", bad.stderr)
        self.assertEqual(len(run.calls), 2)
        with self.assertRaises(AssertionError):
            run(["ffmpeg", "-i", "x"])
        clock = Clock(datetime(2031, 7, 8, 6, 0), tz=self.cfg.tz()).patch(self, self.cfg)
        self.assertEqual(self.cfg.now().hour, 6)
        clock.advance(minutes=90)
        self.assertEqual((self.cfg.now().hour, self.cfg.now().minute), (7, 30))
        self.assertEqual(clock.at("21:05").hour, 21)
        self.assertEqual(Clock(datetime(2031, 1, 1)).now().tzinfo, timezone.utc)

    def test_3_posted_slots_look_like_the_posters(self):
        s = slot_posted(self, self.gta, DAY, "07:00", "08:45")
        for k in ("07:00", "08:45"):
            self.assertEqual(s[k]["status"], B.SCHEDULED)
            self.assertEqual(B.verify(s[k]), [])
            fb, ig, yt = (s[k]["outlets"][o] for o in ("facebook_reels", "instagram_reels", "youtube_shorts"))
            self.assertEqual((fb["provider"], ig["provider"]), ("direct", "direct"))
            self.assertEqual(fb["published_at"], datetime.fromisoformat(s[k]["publish_at"]).astimezone(
                timezone.utc).isoformat())
            self.assertEqual(yt["publish_at"], s[k]["publish_at"])
            self.assertEqual(fb["clip_sha"], s[k]["clip"]["sha256"])
        self.assertNotEqual(s["07:00"]["outlets"]["facebook_reels"]["post_id"],
                            s["08:45"]["outlets"]["facebook_reels"]["post_id"])
        self.assertEqual(Path(self.cfg.drafts_root), self.tmp / "drafts")
        self.assertEqual(C.STATE, self.tmp / "state")
        self.assertEqual(D.TOKENS, self.tmp / "keys")


if __name__ == "__main__":
    unittest.main()
