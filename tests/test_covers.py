import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""THE COVER (clipfactory/covers.py, 2026-10-28, Andrew's gaps review: no cover was set on any platform). The cover
is the engine's poster found in the very file each platform gets (a VS card in front moves it; a frame like no other
is no cover), Instagram gets thumb_offset at it, Facebook gets that frame as the Reel's thumbnail once the Reel is
up, nothing changes while the account's cover switch is off, and no cover problem of any kind ever fails a post.
Tiny synthetic clips in a throwaway folder, Meta faked; no network."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipfactory import collect as K        # noqa: E402
from clipfactory import covers as CV        # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import poster as P         # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import signoff as S        # noqa: E402
from clipfactory import store               # noqa: E402
from clipfactory import switches as SW      # noqa: E402
from test_posting_fixes import DAY, Base, FakeMeta   # noqa: E402

SIZE = "180x320"
OPENER_S = 0.8            # a stand-in VS card in front of the clip
POSTER_S = 1.0            # the engine's poster: this second of the clip as rendered


def make(work: Path) -> tuple:
    """(the clip as collected: a red opener, then the rendered clip; the engine's poster of the rendered clip)."""
    work.mkdir(parents=True, exist_ok=True)
    clip, jpg = work / "posted.mp4", work / "poster.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={SIZE}:r=30:d={OPENER_S}",
                    "-f", "lavfi", "-i", f"testsrc2=s={SIZE}:r=30:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-map", "2:a", "-t", str(OPENER_S + 2), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={SIZE}:r=30:d=2",
                    "-ss", str(POSTER_S), "-frames:v", "1", "-q:v", "2", str(jpg)], check=True)
    return clip, jpg


def fast(work: Path, n: int) -> tuple:
    """(a fast pan across white bars at irregular places, 7 pixels a frame at 30 a second; its frame n as the
    poster)."""
    work.mkdir(parents=True, exist_ok=True)
    clip, jpg = work / "fast.mp4", work / f"fast-{n}.jpg"
    bars = ",".join(f"drawbox=x={x}:y=0:w={w}:h=320:color=white:t=fill"
                    for x, w in ((40, 30), (150, 60), (290, 20), (380, 80), (520, 25)))
    chain = f"color=c=black:s=600x320:r=30:d=2,{bars},crop=180:320:'n*7':0"
    if not clip.is_file():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", chain, "-c:v", "libx264", "-preset",
                        "ultrafast", "-pix_fmt", "yuv420p", str(clip)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{chain},select=eq(n\\,{n})",
                    "-frames:v", "1", "-q:v", "2", str(jpg)], check=True)
    return clip, jpg


class CoverMeta(FakeMeta):
    """FakeMeta with the video thumbnails edge, keeping each Instagram container's form and each thumbnail sent."""

    def __init__(self):
        super().__init__()
        self.ig_forms, self.thumbs = [], []

    def __call__(self, method, url, params=None, form=None, body=None, data=None, headers=None, timeout=0):
        u = url.split("?")[0]
        if u.endswith("/thumbnails"):
            self.calls.append("fb_thumb")
            if "fb_thumb" in self.fail_before:
                raise self.fail_before.pop("fb_thumb")
            self.thumbs.append({"url": u, "data": data, "headers": dict(headers or {}), "params": params,
                                "form": form})
            return {"success": True}
        if u.endswith("/media") and method == "POST":
            self.ig_forms.append(dict(form or {}))
        return super().__call__(method, url, params=params, form=form, body=body, data=data, headers=headers,
                                timeout=timeout)


class FindTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-covers-"))
        cls.clip, cls.jpg = make(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_1_the_poster_is_found_past_the_opener(self):
        at, dist = CV.find(self.clip, self.jpg)
        self.assertAlmostEqual(at, OPENER_S + POSTER_S, delta=0.15)
        self.assertLess(dist, CV.MATCH_MAX)

    def test_2_a_still_like_no_frame_is_no_cover(self):
        blue = self.tmp / "blue.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={SIZE}:d=0.1",
                        "-frames:v", "1", str(blue)], check=True)
        at, dist = CV.find(self.clip, blue)
        self.assertIsNone(at)
        self.assertGreater(dist, CV.MATCH_MAX)

    def test_3_the_frame_it_gives_facebook_is_the_poster(self):
        at, _ = CV.find(self.clip, self.jpg)
        out = CV.frame(self.clip, at, self.tmp / "cover.jpg")
        self.assertEqual(out.read_bytes()[:2], b"\xff\xd8")                  # a JPEG
        again, dist = CV.find(self.clip, out)
        self.assertAlmostEqual(again, at, delta=0.11)
        self.assertLess(dist, 3)

    def test_4_the_switch_is_a_part_with_a_doctor_line(self):
        self.assertEqual(SW.MODES["cover"], ("off", "on"))
        self.assertIn("covers", REG.DOCTORS)

    def test_5_a_fast_shot_is_found_by_the_second_look(self):
        for n in range(30, 36):                 # a frame the first look steps over, whichever ones ffmpeg keeps
            clip, jpg = fast(self.tmp / "fast", n)
            first = abs(CV._grey(clip, f"fps={CV.FPS}") - CV._grey(jpg, "null")[0]).mean(axis=(1, 2)).min()
            if first > CV.MATCH_MAX:
                break
        self.assertGreater(first, CV.MATCH_MAX)                  # ten frames a second step over it
        at, dist = CV.find(clip, jpg)
        self.assertAlmostEqual(at, n / 30, delta=0.04)
        self.assertLess(dist, CV.MATCH_MAX)

    def test_6_a_cut_without_the_poster_gets_the_frame_most_like_it(self):
        with mock.patch.object(CV, "MATCH_MAX", -1.0):
            self.assertEqual(CV._search(self.clip, self.jpg)[0], None)            # no skip given: no look-alike
            at, dist, how = CV._search(self.clip, self.jpg, skip=OPENER_S)
        self.assertEqual(how, "look_alike")
        self.assertAlmostEqual(at, OPENER_S + POSTER_S, delta=0.15)
        self.assertLessEqual(dist, CV.LOOK_MAX)

    def test_7_the_look_alike_is_never_in_the_opener(self):
        red = self.tmp / "red.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={SIZE}:d=0.1",
                        "-frames:v", "1", str(red)], check=True)
        with mock.patch.object(CV, "MATCH_MAX", -1.0):
            inside, _, _ = CV._search(self.clip, red, skip=0.0)
            past, _, _ = CV._search(self.clip, red, skip=OPENER_S)
        self.assertLess(inside, OPENER_S)                        # told of no opener, the red card is most like it
        self.assertTrue(past is None or past >= OPENER_S + CV.SKIP_PAD - 0.05)

    def test_8_the_opener_is_what_the_factory_put_in_front(self):
        ig = "/drafts/0630 x/instagram_reels.mp4"
        slot = {"clip": {"path": "/drafts/0630 x/01-x.mp4", "seconds": 87.3},
                "cuts": {"instagram_reels": {"path": ig, "seconds": 37.533, "delivered_seconds": 34.5}}}
        self.assertAlmostEqual(CV.opener(slot, Path(ig)), 3.033, places=3)
        self.assertEqual(CV.opener(slot, Path("/drafts/0630 x/01-x.mp4")), 0.0)   # nothing in front of it
        self.assertEqual(CV.opener(slot, Path("/elsewhere.mp4")), 0.0)
        self.assertEqual(CV.opener({}, Path(ig)), 0.0)

    def test_9_a_fight_is_its_vs_card_when_the_file_opens_on_it(self):
        ig, clip = "/drafts/0750 x/instagram_reels.mp4", "/drafts/0750 x/01-x.mp4"
        slot = {"vs_card": {"seconds": 3.2, "vs_at": 2.85},
                "clip": {"path": clip, "seconds": 87.3, "delivered_seconds": 84.1},
                "cuts": {"instagram_reels": {"path": ig, "seconds": 37.5}}}           # no room: no card in front
        self.assertEqual(CV.vs_moment(slot, Path(clip)), 2.85)
        self.assertIsNone(CV.vs_moment(slot, Path(ig)))
        self.assertIsNone(CV.vs_moment({**slot, "vs_card": {"seconds": 3.2}}, Path(clip)))   # made before 10-30
        self.assertIsNone(CV.vs_moment({**slot, "vs_card": {"seconds": 3.2, "vs_at": 3.4}}, Path(clip)))
        self.assertIsNone(CV.vs_moment({"clip": slot["clip"]}, Path(clip)))


class PostTest(Base):
    """Through post_due with the real Facebook and Instagram posters and a faked Graph API."""

    def setUp(self):
        super().setUp()
        self.clip, self.jpg = make(self.tmp / "made")
        self.fake = self.meta_cover()

    def meta_cover(self) -> CoverMeta:
        fake = CoverMeta()
        D._save(self.acct, "meta", {"page_id": "P1", "page_token": "PT", "ig_user_id": "IG1"})
        self.patch(D, "_http", fake)
        self.patch(D.time, "sleep", lambda s: None)
        return fake

    def switch(self, value: str) -> None:
        with store.edit(store.path(self.acct.slug, "social", "settings.json"), dict) as d:
            d.setdefault("modes", {})["cover"] = value

    def ready_with_poster(self, k: str = "07:00", poster: bool = True) -> None:
        d = self.acct.delivered_dirs(DAY)[0] / f"{k.replace(':', '')} cover-{k.replace(':', '')}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.clip, d / "01-x.mp4")
        if poster:
            shutil.copy(self.jpg, d / "01-x.jpg")
        for p in ("youtube", "tiktok", "facebook", "reels"):
            (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
        K.collect(self.cfg, self.acct, DAY)
        S.approve(self.cfg, self.acct, DAY, [k], "Andrew")

    def post(self, k: str = "07:00") -> dict:
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        return self.slot(k)["outlets"]

    def test_1_off_posts_exactly_as_before(self):
        self.ready_with_poster()
        out = self.post()
        self.assertEqual((out["facebook_reels"]["post_id"], out["instagram_reels"]["post_id"]), ("V1", "M3"))
        self.assertNotIn("cover", out["facebook_reels"])
        self.assertNotIn("cover", out["instagram_reels"])
        self.assertNotIn("thumb_offset", self.fake.ig_forms[0])
        self.assertNotIn("fb_thumb", self.fake.calls)

    def test_2_on_instagram_gets_thumb_offset_and_facebook_the_frame(self):
        self.switch("on")
        self.ready_with_poster()
        out = self.post()
        ms = int(self.fake.ig_forms[0]["thumb_offset"])
        self.assertAlmostEqual(ms, (OPENER_S + POSTER_S) * 1000, delta=150)
        ig, fb = out["instagram_reels"], out["facebook_reels"]
        self.assertEqual((ig["post_id"], ig["cover"]["how"], ig["cover"]["ms"]), ("M3", "thumb_offset", ms))
        self.assertEqual((fb["post_id"], fb["cover"]["how"], fb["cover"]["set"]), ("V1", "thumbnail", True))
        # after the Reel is up, never before; the Page's key in the body, never in the address
        self.assertLess(self.fake.calls.index("fb_finish"), self.fake.calls.index("fb_thumb"))
        t = self.fake.thumbs[0]
        self.assertTrue(t["url"].endswith("/V1/thumbnails"))
        self.assertNotIn("PT", t["url"])
        self.assertIn(b'name="is_preferred"\r\n\r\ntrue', t["data"])
        self.assertIn(b'name="source"; filename="cover.jpg"\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8', t["data"])
        self.assertTrue(t["headers"]["Content-Type"].startswith("multipart/form-data; boundary="))

    def test_3_a_refused_thumbnail_leaves_the_reel_posted(self):
        self.switch("on")
        self.ready_with_poster()
        self.fake.fail_before["fb_thumb"] = D.PlatformError("HTTP 403 from graph.facebook.com: (#200) Missing Permissions")
        fb = self.post()["facebook_reels"]
        self.assertEqual((fb["post_id"], fb.get("tries"), fb.get("last_error")), ("V1", None, None))
        self.assertEqual(fb["cover"]["set"], False)
        self.assertIn("Missing Permissions", fb["cover"]["why"])
        self.assertEqual(self.fake.calls.count("fb_start"), 1)

    def test_4_no_poster_still_is_no_cover_and_the_clip_goes(self):
        self.switch("on")
        self.ready_with_poster(poster=False)
        out = self.post()
        self.assertEqual((out["facebook_reels"]["post_id"], out["instagram_reels"]["post_id"]), ("V1", "M3"))
        self.assertIn("no cover still", out["instagram_reels"]["cover"]["none"])
        self.assertNotIn("thumb_offset", self.fake.ig_forms[0])
        self.assertNotIn("fb_thumb", self.fake.calls)

    def test_5_any_error_in_the_cover_never_fails_a_post(self):
        self.switch("on")
        self.ready_with_poster()
        with mock.patch.object(CV, "_search", side_effect=RuntimeError("boom")):
            out = self.post()
        self.assertEqual((out["facebook_reels"]["post_id"], out["instagram_reels"]["post_id"]), ("V1", "M3"))
        for plat in ("facebook_reels", "instagram_reels"):
            self.assertIn("boom", out[plat]["cover"]["none"])
            self.assertIsNone(out[plat].get("tries"))
        self.assertNotIn("thumb_offset", self.fake.ig_forms[0])

    def test_6_a_poster_like_no_frame_of_the_file_is_no_cover(self):
        self.switch("on")
        self.ready_with_poster()
        with mock.patch.object(CV, "MATCH_MAX", -1.0), mock.patch.object(CV, "LOOK_MAX", -1.0):
            out = self.post()
        self.assertIn("no frame of", out["instagram_reels"]["cover"]["none"])
        self.assertNotIn("thumb_offset", self.fake.ig_forms[0])
        self.assertNotIn("fb_thumb", self.fake.calls)

    def test_6c_a_fight_gets_its_vs_card_even_with_no_poster(self):
        self.switch("on")
        clip = self.tmp / "made" / "posted.mp4"
        slot = {"vs_card": {"seconds": 3.1, "vs_at": 2.35},
                "clip": {"path": str(clip), "seconds": 90.0, "delivered_seconds": 86.9}}
        self.assertEqual(CV.pick(self.acct, slot, clip), (2.35, {"at_s": 2.35, "from": "vs_card"}))
        self.switch("off")
        self.assertEqual(CV.pick(self.acct, slot, clip), (None, {}))

    def test_6b_a_poster_not_in_the_file_gets_the_frame_most_like_it(self):
        self.switch("on")
        self.ready_with_poster()
        with mock.patch.object(CV, "MATCH_MAX", -1.0):
            out = self.post()
        ig, fb = out["instagram_reels"], out["facebook_reels"]
        self.assertEqual((ig["post_id"], ig["cover"]["look_alike"], ig["cover"]["how"]), ("M3", True, "thumb_offset"))
        self.assertAlmostEqual(int(self.fake.ig_forms[0]["thumb_offset"]), (OPENER_S + POSTER_S) * 1000, delta=150)
        self.assertEqual((fb["post_id"], fb["cover"]["look_alike"], fb["cover"]["set"]), ("V1", True, True))

    def test_7_a_container_refused_with_the_cover_goes_without_it(self):
        self.switch("on")
        self.ready_with_poster()
        real = self.fake.act

        def act(step, u, form):
            if step == "ig_create" and "thumb_offset" in form:
                raise D.PlatformError("HTTP 400 from graph.facebook.com: (#100) Invalid parameter")
            return real(step, u, form)
        self.fake.act = act
        ig = self.post()["instagram_reels"]
        self.assertEqual((ig["post_id"], ig.get("tries")), ("M3", None))
        self.assertIn("went without", ig["cover"]["none"])
        self.assertEqual([("thumb_offset" in f) for f in self.fake.ig_forms], [True, False])

    def test_8_the_doctor_says_the_switch_and_how_to_flip_it(self):
        lines = [w for _, w, _ in CV.doctor(self.cfg)]
        self.assertTrue(any(f"factory.py social on {self.acct.slug} cover" in w for w in lines))
        self.switch("on")
        self.assertTrue(any(w.startswith(f"{self.acct.name} cover: on") for _, w, _ in CV.doctor(self.cfg)))


if __name__ == "__main__":
    unittest.main()
