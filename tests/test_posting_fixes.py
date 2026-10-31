import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"]="1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())
# The posting fixes of the 2026-10-17 audit (PP-1 to PP-19, YTH-2/8/13/14, STA-1/4): a lost answer is looked up, never
# posted twice; a Pull while a post is on its way; only the live state posts; the hand note never reaches a caption;
# YouTube spread out; stand-ins; the drop folder; Post to TikTok now; post-due's health; atomic keys; slots signed by
# hand stay by hand. Then the reviewer's eight edge cases (ReviewFindingsTest) and the review page's hooks.
# Throwaway folders and fake platforms only: nothing can reach the network (clipfactory/net.py).
import io
import json
import shutil
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import copytext as T   # noqa: E402
from clipfactory import direct as D     # noqa: E402
from clipfactory import net             # noqa: E402
from clipfactory import outlets as O    # noqa: E402
from clipfactory import poster as P     # noqa: E402
from clipfactory import signoff as S    # noqa: E402

DAY = date(2031, 5, 6)


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def clip(path: Path, seconds: float, color: str = "black") -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class Resp:
    def __init__(self, body=None, headers=None):
        self.body, self.headers = body, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body or {}).encode()


class FakeMeta:
    """Facebook Reels and Instagram Reels as the Graph API answers them. A step can fail before it acts, or after it
    acted with its answer lost (fail_after: the platform did it, the caller never heard)."""

    def __init__(self):
        self.calls, self.n = [], 0
        self.videos, self.containers, self.captions, self.media = {}, {}, {}, []
        self.fail_before, self.fail_after, self.hooks = {}, {}, {}
        self.published = []

    def step(self, method, u, form):
        if u.endswith("/video_reels") and method == "POST":
            return "fb_" + form.get("upload_phase", "")
        if "video-upload" in u:
            return "fb_upload"
        if "ig-api-upload" in u:
            return "ig_upload"
        if u.endswith("/media_publish"):
            return "ig_publish"
        if u.endswith("/media"):
            return "ig_create" if method == "POST" else "ig_list"
        if u.strip("/").startswith("V"):
            return "fb_status"
        if u.strip("/").startswith("C"):
            return "ig_status"
        if u.strip("/").startswith("M"):
            return "ig_info"
        raise AssertionError(f"unexpected call {method} {u}")

    def __call__(self, method, url, params=None, form=None, body=None, data=None, headers=None, timeout=0):
        u = url.split("?")[0].replace(D.GRAPH, "")
        form = dict(form or {})
        step = self.step(method, u, form)
        self.calls.append(step)
        if step in self.hooks:
            self.hooks.pop(step)()
        if step in self.fail_before:
            raise self.fail_before.pop(step)
        out = self.act(step, u, form)
        if step in self.fail_after:
            raise self.fail_after.pop(step)
        return out

    def act(self, step, u, form):
        if step == "fb_start":
            self.n += 1
            vid = f"V{self.n}"
            self.videos[vid] = "not_started"
            return {"video_id": vid, "upload_url": f"https://rupload.facebook.com/video-upload/v25.0/{vid}"}
        if step in ("fb_upload", "ig_upload"):
            return {"success": True}
        if step == "fb_finish":
            self.videos[form["video_id"]] = "complete"
            self.published.append(form["video_id"])
            return {"success": True}
        if step == "fb_status":
            vid = u.strip("/")
            if vid not in self.videos:
                raise D.PlatformError(f"HTTP 400 from graph.facebook.com: Object with ID '{vid}' does not exist")
            ph = self.videos[vid]
            return {"status": {"video_status": "ready", "publishing_phase": {"status": ph}},
                    "permalink_url": f"/reel/{vid}" if ph == "complete" else "", "created_time": "2031-05-06T13:00:00+0000"}
        if step == "ig_create":
            self.n += 1
            cid = f"C{self.n}"
            self.containers[cid], self.captions[cid] = "FINISHED", form.get("caption", "")
            return {"id": cid, "uri": f"https://rupload.facebook.com/ig-api-upload/v25.0/{cid}"}
        if step == "ig_status":
            return {"status_code": self.containers[u.strip("/")]}
        if step == "ig_publish":
            cid = form["creation_id"]
            if self.containers[cid] == "PUBLISHED":
                raise D.PlatformError("HTTP 400 from graph.facebook.com: that container is published already")
            self.containers[cid] = "PUBLISHED"
            self.n += 1
            mid = f"M{self.n}"
            self.media.insert(0, {"id": mid, "caption": self.captions[cid], "timestamp": "2031-05-06T13:00:00+0000"})
            self.published.append(mid)
            return {"id": mid}
        if step == "ig_info":
            return {"permalink": f"https://www.instagram.com/reel/{u.strip('/')}", "timestamp": "2031-05-06T13:00:00+0000"}
        if step == "ig_list":
            return {"data": list(self.media)}
        raise AssertionError(step)


class FakeYouTube:
    """YouTube's resumable upload: a session per upload, a query that says what it has, the channel's uploads."""

    def __init__(self):
        self.sessions, self.videos, self.inits, self.puts = {}, [], 0, 0
        self.lose_put = self.half_put = 0

    def urlopen(self, req, timeout=0, data=None):
        url, method = req.full_url, req.get_method()
        if "upload/youtube/v3/videos" in url and method == "POST":
            self.inits += 1
            sess = f"https://upload.example/session{self.inits}"
            self.sessions[sess] = {"got": 0, "size": int(req.get_header("X-upload-content-length")), "vid": "",
                                   "title": json.loads(req.data)["snippet"]["title"]}
            return Resp(headers={"Location": sess})
        if url in self.sessions and method == "PUT":
            s = self.sessions[url]
            rng = req.get_header("Content-range") or ""
            if rng.startswith("bytes */"):                        # what became of it?
                if s["vid"]:
                    return Resp({"id": s["vid"]})
                raise urllib.error.HTTPError(url, 308, "Resume Incomplete",
                                             {"Range": f"bytes=0-{s['got'] - 1}"} if s["got"] else {}, io.BytesIO(b""))
            self.puts += 1
            start = int(rng.split()[1].split("-")[0]) if rng else 0
            if self.half_put:
                self.half_put -= 1
                s["got"] = start + len(req.data) // 2
                raise TimeoutError("timed out")
            s["got"] = start + len(req.data)
            s["vid"] = f"Y{len(self.videos) + 1}"
            self.videos.append({"id": s["vid"], "title": s["title"], "at": datetime.now(timezone.utc)})
            if self.lose_put:
                self.lose_put -= 1
                raise TimeoutError("timed out")                   # YouTube has it; the answer never came
            return Resp({"id": s["vid"]})
        if url in self.gone and method == "PUT":
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        raise AssertionError(f"unexpected {method} {url}")

    gone: set = set()

    def http(self, method, url, params=None, form=None, body=None, data=None, headers=None, timeout=0):
        if url.endswith("/channels"):
            return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
        if url.endswith("/playlistItems"):
            return {"items": [{"contentDetails": {"videoId": v["id"]}} for v in reversed(self.videos)]}
        if url.endswith("/videos"):
            return {"items": [{"id": v["id"], "snippet": {"title": v["title"],
                                                          "publishedAt": v["at"].isoformat().replace("+00:00", "Z")},
                               "contentDetails": {"duration": "PT2S"}, "status": {"uploadStatus": "uploaded"}}
                              for v in self.videos if v["id"] in params["id"].split(",")]}
        raise AssertionError(url)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-posting-"))
        cls.old_state, C.STATE = C.STATE, cls.tmp / "state"
        cls.old_tokens = (O.TOKENS, D.TOKENS)
        O.TOKENS = D.TOKENS = cls.tmp / "keys"
        raw = pin_politics(json.loads((ROOT / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(cls.tmp / "drafts")
        raw["inbox_root"] = str(cls.tmp / "queue")
        raw["review_port"] = 0
        raw.pop("inbox_auto_sign", None)           # the owner's switches are each test's own business
        raw.pop("auto_sign", None)  # the automatic sign-off (autosign.py) is tests/test_autosign.py's; these are the Approve-only words
        for a in raw["accounts"].values():
            a.pop("signed_by_hand_stays", None)
            a["delivered"] = [str(cls.tmp / "delivered" / a["name"] / "{YYYY}{MM}{DD}")]
            a["seconds"] = {"min": 1, "max": 3}
            a["outlets"] = {"youtube_shorts": {"mode": "api"}, "facebook_reels": {"mode": "api"},
                            "instagram_reels": {"mode": "api"}, "tiktok": {"mode": "export"}}
        cls.raw = raw
        cls.cfg_path = cls.tmp / "accounts.json"
        cls.cfg_path.write_text(json.dumps(raw))
        cls.master = cls.tmp / "master.mp4"
        clip(cls.master, 2.0)
        cls.other = cls.tmp / "other.mp4"
        clip(cls.other, 2.0, "white")

    @classmethod
    def tearDownClass(cls):
        C.STATE = cls.old_state
        O.TOKENS, D.TOKENS = cls.old_tokens

    def setUp(self):
        for d in ("state", "drafts", "delivered", "keys", "queue"):
            shutil.rmtree(self.tmp / d, ignore_errors=True)
        self.cfg_path.write_text(json.dumps(self.raw))
        self.cfg = C.load(self.cfg_path)
        self.acct = self.cfg.account("gaming")
        self.calls = []
        self.saved = []

    def tearDown(self):
        for obj, name, value in reversed(self.saved):
            setattr(obj, name, value)

    def patch(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    # ------------------------------------------------------------------------------------------------- helpers
    def ready(self, *slots: str, day: date = DAY, slug: str = "clip") -> None:
        root = self.acct.delivered_dirs(day)[0]
        for k in slots:
            d = root / f"{k.replace(':', '')} {slug}-{k.replace(':', '')}"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.master, d / "01-x.mp4")
            for p in ("youtube", "tiktok", "facebook", "reels"):
                (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
        K.collect(self.cfg, self.acct, day)

    def slot(self, k: str, day: date = DAY) -> dict:
        return B.read(self.acct, day)["slots"][k]

    def at(self, hhmm: str, day: date = DAY) -> datetime:
        return self.cfg.slot_time(day, hhmm)

    def posters(self):
        def make(plat):
            def post(acct, path, words, slot):
                self.calls.append((plat, slot["slot"]))
                return {"post_id": f"{plat}-{slot['slot']}", "url": f"https://x/{plat}/{slot['slot']}"}
            return post
        return {p: make(p) for p in ("facebook_reels", "instagram_reels", "tiktok")}

    def uploader(self, then=None):
        def up(acct, slot):
            self.calls.append(("youtube_shorts", slot["slot"]))
            if then:
                then()
            return f"vid{len(self.calls)}"
        return up

    def connect_youtube(self):
        tf = O.yt_token_file(self.acct)
        tf.parent.mkdir(parents=True, exist_ok=True)
        tf.write_text(json.dumps({"access_token": "AT", "expires_at": 9e12, "client_id": "c", "client_secret": "s",
                                  "refresh_token": "r", "scope": "youtube.upload youtube.readonly"}))

    def meta(self) -> FakeMeta:
        fake = FakeMeta()
        D._save(self.acct, "meta", {"page_id": "P1", "page_token": "PT", "ig_user_id": "IG1"})
        self.patch(D, "_http", fake)
        self.patch(D.time, "sleep", lambda s: None)
        return fake

    def real_meta_posters(self):
        return {"facebook_reels": D.fb_post, "instagram_reels": D.ig_post}


class LostAnswersTest(Base):
    """PP-1 / YTH-8 / PP-9: what the platform already has is looked up, never posted a second time."""

    def test_1_a_failed_read_after_facebook_published_is_a_post(self):
        fake = self.meta()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.fail_before["fb_status"] = D.PlatformError("HTTP 500 from graph.facebook.com: Please retry")
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        fb = self.slot("07:00")["outlets"]["facebook_reels"]
        self.assertEqual((fb["post_id"], fb.get("tries")), ("V1", None))      # posted, the link comes later
        for t in ("07:12", "07:30", "08:30"):
            P.post_due(self.cfg, now=self.at(t), posters=self.real_meta_posters(), uploader=self.uploader())
        self.assertEqual([v for v in fake.published if v.startswith("V")], ["V1"])   # one reel, never two
        self.assertEqual(fake.calls.count("fb_start"), 1)

    def test_2_a_lost_publish_answer_is_looked_up(self):
        fake = self.meta()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.fail_after["fb_finish"] = D.PlatformError("timed out")          # Facebook published; no answer
        fake.fail_after["ig_publish"] = D.PlatformError("timed out")         # Instagram too
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        s = self.slot("07:00")["outlets"]
        self.assertEqual((s["facebook_reels"]["tries"], s["facebook_reels"]["attempt"]["video_id"]), (1, "V1"))
        self.assertEqual(s["instagram_reels"]["attempt"]["container_id"], "C2")
        P.post_due(self.cfg, now=self.at("07:12"), posters=self.real_meta_posters(), uploader=self.uploader())
        s = self.slot("07:00")["outlets"]
        self.assertEqual(s["facebook_reels"]["post_id"], "V1")
        self.assertEqual(s["instagram_reels"]["post_id"], "M3")              # found by its caption
        self.assertNotIn("attempt", s["facebook_reels"])
        self.assertEqual(fake.calls.count("fb_start") + fake.calls.count("ig_create"), 2)
        self.assertEqual(fake.calls.count("ig_publish"), 1)
        self.assertEqual(len(fake.published), 2)

    def test_3_a_failed_link_read_after_instagram_published_is_a_post(self):
        fake = self.meta()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.fail_before["ig_info"] = D.PlatformError("HTTP 500 from graph.facebook.com: try again")
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        P.post_due(self.cfg, now=self.at("07:20"), posters=self.real_meta_posters(), uploader=self.uploader())
        ig = self.slot("07:00")["outlets"]["instagram_reels"]
        self.assertTrue(ig["post_id"].startswith("M"))
        self.assertEqual(fake.calls.count("ig_publish"), 1)

    def test_4_a_reel_facebook_failed_after_the_publish_is_posted_again(self):
        fake = self.meta()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        real_act = fake.act

        def broken(step, u, form):
            out = real_act(step, u, form)
            if step == "fb_status" and u.strip("/") == "V1":
                out["status"]["video_status"] = "error"
            return out
        fake.act = broken
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        fb = self.slot("07:00")["outlets"]["facebook_reels"]
        self.assertNotIn("post_id", fb)                      # not up: one of the tries
        self.assertEqual(fb["tries"], 1)
        P.post_due(self.cfg, now=self.at("07:12"), posters=self.real_meta_posters(), uploader=self.uploader())
        self.assertNotEqual(self.slot("07:00")["outlets"]["facebook_reels"]["post_id"], "V1")

    def youtube(self) -> FakeYouTube:
        fake = FakeYouTube()
        FakeYouTube.gone = set()
        self.connect_youtube()
        self.patch(urllib.request, "urlopen", fake.urlopen)
        self.patch(net, "guard", lambda what: None)
        self.patch(D, "_http", fake.http)
        return fake

    def test_5_a_lost_youtube_answer_is_read_from_its_session(self):
        fake = self.youtube()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.lose_put = 1
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=O.youtube_upload)
        yt = self.slot("07:00")["outlets"]["youtube_shorts"]
        self.assertEqual((yt["tries"], yt["attempt"]["session"]), (1, "https://upload.example/session1"))
        P.post_due(self.cfg, now=self.at("05:41"), posters=self.posters(), uploader=O.youtube_upload)
        yt = self.slot("07:00")["outlets"]["youtube_shorts"]
        self.assertEqual((yt["video_id"], fake.inits, len(fake.videos)), ("Y1", 1, 1))
        self.assertNotIn("attempt", yt)

    def test_6_a_gone_session_is_found_on_the_channel(self):
        fake = self.youtube()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.lose_put = 1
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=O.youtube_upload)
        FakeYouTube.gone = set(fake.sessions)
        fake.sessions.clear()                                  # the session expired
        P.post_due(self.cfg, now=self.at("05:41"), posters=self.posters(), uploader=O.youtube_upload)
        self.assertEqual((self.slot("07:00")["outlets"]["youtube_shorts"]["video_id"], fake.inits), ("Y1", 1))

    def test_7_half_an_upload_is_finished_not_sent_again(self):
        fake = self.youtube()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.half_put = 1
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=O.youtube_upload)
        P.post_due(self.cfg, now=self.at("05:41"), posters=self.posters(), uploader=O.youtube_upload)
        self.assertEqual((self.slot("07:00")["outlets"]["youtube_shorts"]["video_id"], fake.inits, fake.puts),
                         ("Y1", 1, 2))

    def test_8_nothing_on_youtube_means_a_new_upload(self):
        fake = self.youtube()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.half_put = 1
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=O.youtube_upload)
        FakeYouTube.gone = set(fake.sessions)
        fake.sessions.clear()
        P.post_due(self.cfg, now=self.at("05:41"), posters=self.posters(), uploader=O.youtube_upload)
        self.assertEqual((self.slot("07:00")["outlets"]["youtube_shorts"]["video_id"], fake.inits), ("Y1", 2))

    def test_9_pull_and_try_again_keep_what_was_sent(self):
        fake = self.meta()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.fail_after["fb_finish"] = D.PlatformError("timed out")
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertEqual(self.slot("07:00")["outlets"]["facebook_reels"], {"attempt": fake_attempt(self, "facebook_reels")})
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.at("07:30"), posters=self.real_meta_posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["facebook_reels"]["post_id"], "V1")
        self.assertEqual(fake.calls.count("fb_start"), 1)


def fake_attempt(t: Base, plat: str) -> dict:
    return t.slot("07:00")["outlets"][plat]["attempt"]


class PullWhileSendingTest(Base):
    """PP-2 and PP-10: a Pull while a post is on its way, and a clip signed again."""

    def test_1_pull_before_facebook_publishes_stops_it(self):
        fake = self.meta()
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.hooks["fb_upload"] = lambda: S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        out = P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        self.assertNotIn("fb_finish", fake.calls)
        self.assertIn("pulled or signed again", " ".join(out))
        s = self.slot("07:00")
        self.assertEqual((s["status"], s.get("outlets")), (B.READY, {}))     # no try, no give-up, nothing out

    def test_2_an_upload_that_lands_after_pull_is_kept_and_flagged(self):
        self.ready("07:00")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        up = self.uploader(then=lambda: S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew"))
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=up)
        s = self.slot("07:00")
        self.assertEqual(s["status"], B.READY)
        self.assertEqual(s["outlets"]["youtube_shorts"]["video_id"], "vid1")
        self.assertIn("UPLOADED WHILE YOU PULLED IT", B.last_note(s))
        self.assertIn("make it private", P.flag_words(s))
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")       # the same clip again: it stays
        self.assertIn("YouTube keeps its upload", out[0])
        self.assertEqual(P.flag_words(self.slot("07:00")), "")

    def test_3_pull_says_when_youtube_is_sending_it_right_then(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P._busy(self.acct, DAY, "07:00", "youtube_shorts")
        try:
            out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        finally:
            P._busy_done()
        self.assertIn("YouTube was sending it right then", out[0])

    def test_4_a_changed_clip_signed_again_sets_the_old_upload_aside(self):
        self.ready("07:00")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=self.uploader())
        out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("To stop that, make it private in Studio", out[0])
        self.assertIn("To keep it, approve the same clip again", out[0])
        shutil.copy(self.other, self.slot("07:00")["clip"]["path"])          # a fixed clip
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("YouTube has the OLD clip (vid1)", out[0])
        yt = self.slot("07:00")["outlets"]["youtube_shorts"]
        self.assertEqual((yt["gave_up"], yt["old"]["video_id"]), (True, "vid1"))
        P.post_due(self.cfg, now=self.at("05:40"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(len(self.calls), 1)                                  # waits for the person
        S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.at("05:50"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["youtube_shorts"]["video_id"], "vid2")


class GateTest(Base):
    """PP-3: only the live state reaches a platform, whatever the keys."""

    def test_1_a_copy_of_the_state_never_posts(self):
        env = {k: os.environ.get(k) for k in ("CLIP_FACTORY_OFFLINE", "CLIP_FACTORY_ALLOW_NETWORK_IN_TESTS",
                                              "CLIP_FACTORY_ALLOW_NETWORK_FROM_COPY")}
        try:
            os.environ.pop("CLIP_FACTORY_OFFLINE")
            os.environ["CLIP_FACTORY_ALLOW_NETWORK_IN_TESTS"] = "1"
            os.environ.pop("CLIP_FACTORY_ALLOW_NETWORK_FROM_COPY", None)
            self.assertEqual(net.why_not(), "not the live state, a copy")
            with self.assertRaises(net.Offline) as e:
                net.guard("a YouTube upload")
            self.assertIn("copy of the state", str(e.exception))
            self.assertEqual(P.kick(self.cfg), "posting not started (not the live state, a copy)")
            self.assertFalse(P.spawn_tiktok_now(self.cfg, self.acct, DAY, "07:00"))
            self.patch(net, "_live_state", lambda: C.STATE)       # this folder were the live one
            self.assertEqual(net.why_not(), "")
        finally:
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertFalse(net.allowed())


class WordsTest(Base):
    """PP-5 / STA-1, PP-7 / YTH-2, YTH-13."""

    def test_1_the_hand_note_never_reaches_a_caption(self):
        text = ("# SLOT 0700\n# Lines starting with # are notes.\n\nTITLE\nA hook\n\nCAPTION\nSenate Banking, July 15 2026."
                "\n\n(no hashtags on facebook, on purpose)\n")
        self.assertEqual(T.for_platform(text, "facebook_reels")["caption"], "A hook\n\nSenate Banking, July 15 2026.")
        self.assertEqual(T.for_platform(text.replace("facebook", "instagram"), "instagram_reels")["caption"],
                         "A hook\n\nSenate Banking, July 15 2026.")     # the hook line leads (10-17)
        kept = "TITLE\nA hook\n\nCAPTION\nWords.\n(no hashtags here, really)\n"
        self.assertTrue(T.for_platform(kept, "facebook_reels")["caption"].endswith("really)"))

    def test_2_youtube_category_tags_and_altered_come_from_the_outlet_and_slot(self):
        sent = []
        self.patch(O, "_yt_upload", lambda token_file, clip, meta, **kw: sent.append(meta) or "vid")
        politics = self.cfg.account("politics")
        for a in (self.acct, politics):
            tf = O.yt_token_file(a)
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text("{}")
        words = self.tmp / "yt.txt"
        words.write_text("A GTA hook\n\nFrom TGG.\n#gta6 #shorts #gaming\n")
        slot = {"slot": "07:00", "publish_at": self.at("07:00").isoformat(), "copy": {"youtube_shorts": {"path": str(words)}},
                "clip": {"path": str(self.master), "seconds": 2.0, "sha256": "x"}}
        politics.outlets["youtube_shorts"]["category_id"] = "25"
        O.youtube_upload(politics, slot)
        O.youtube_upload(self.acct, slot)
        self.assertEqual([m["snippet"]["categoryId"] for m in sent], ["25", "20"])
        self.assertEqual(sent[1]["snippet"]["tags"], [])                      # the switch is off: as before
        self.acct.outlets["youtube_shorts"]["tags_from_hashtags"] = True
        O.youtube_upload(self.acct, dict(slot, altered=True))
        self.assertEqual(sent[2]["snippet"]["tags"], ["gta6", "shorts", "gaming"])
        self.assertTrue(sent[2]["status"]["containsSyntheticMedia"])
        self.assertFalse(sent[1]["status"]["containsSyntheticMedia"])


class SpreadTest(Base):
    """PP-8: with the outlet's switches, YouTube uploads near each slot and never in a burst."""

    def test_1_without_the_switches_everything_uploads_at_once(self):
        self.ready("07:00", "08:45", "10:30")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(len(self.yt()), 3)

    def yt(self):
        return [c[1] for c in self.calls if c[0] == "youtube_shorts"]

    def test_2_two_hours_ahead_and_three_minutes_apart(self):
        self.acct.outlets["youtube_shorts"].update(upload_ahead_hours=2, upload_gap_minutes=3)
        self.ready("07:00", "08:45", "10:30")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, None, "Andrew", now=self.at("05:30"))
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.yt(), ["07:00"])                                # Approve still sends the first at once
        self.assertEqual(P.yt_wait_words(self.cfg, self.acct, self.slot("10:30"), self.at("05:30")),
                         "uploads at 08:30, goes public at 10:30")
        P.post_due(self.cfg, now=self.at("08:31"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.yt(), ["07:00", "08:45"])                       # 10:30 waits 3 minutes behind it
        P.post_due(self.cfg, now=self.at("08:32"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.yt(), ["07:00", "08:45"])
        P.post_due(self.cfg, now=self.at("08:35"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.yt(), ["07:00", "08:45", "10:30"])


class StandInTest(Base):
    """PP-11: a stand-in test clip never reaches a real account."""

    def test_1_approve_and_the_poster_refuse_a_stand_in(self):
        self.ready("07:00", slug="standin-gaming")
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("stand-in", out[0])
        with B.edit(self.cfg, self.acct, DAY) as bd:                          # signed some other way
            s = bd["slots"]["07:00"]
            s["signoff"] = {"by": "Andrew", "at": B.utcnow(), "hash": B.content_hash(s)}
            s["status"] = B.SCHEDULED
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.slot("07:00")["outlets"]["facebook_reels"]["why"], "a stand-in test clip")
        dummy = self.tmp / "01-standin-0700.mp4"
        shutil.copy(self.master, dummy)
        with self.assertRaises(D.Refused):
            D.in_band(self.acct, dummy)

    def test_2_make_standin_refuses_the_live_boards(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("factory_cli", ROOT / "factory.py")
        F = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(F)
        self.patch(C, "STATE", ROOT / "state")         # the tree's own state counts as live; nothing touches it
        with self.assertRaises(SystemExit) as e:
            F.main(["make", "gaming", "--standin", "--date", "2030-01-01"])
        self.assertIn("never on the live boards", str(e.exception))


class DropFolderTest(Base):
    """PP-12: the owner's switch, and iCloud's copies."""

    def drop(self, name: str) -> Path:
        q = self.cfg.inbox_root / self.cfg.account("politics").name
        q.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.master, q / name)
        old = time.time() - 120
        os.utime(q / name, (old, old))
        return q

    def test_1_switched_off_a_drop_waits_for_approve(self):
        from clipfactory import inbox as I
        self.raw["inbox_auto_sign"] = False
        try:
            self.cfg_path.write_text(json.dumps(self.raw))
            cfg = C.load(self.cfg_path)
            acct = cfg.account("politics")
            q = self.drop("senator cornered.mp4")
            out = I.take(cfg, now=cfg.slot_time(DAY, "06:00"))
            s = B.read(acct, DAY)["slots"]["07:00"]
            self.assertEqual((s["status"], s.get("signoff")), (B.READY, None), out)
            self.assertIn("ready to approve", out[0])
            self.assertIn("press Approve", (cfg.inbox_root / "READ ME.txt").read_text())
            self.assertTrue((q / "Taken" / f"{DAY} 0700 senator cornered.mp4").exists())
        finally:
            self.raw.pop("inbox_auto_sign")

    def test_2_an_icloud_copy_of_a_taken_clip_is_refused(self):
        from clipfactory import inbox as I
        acct = self.cfg.account("politics")
        self.drop("senator cornered.mp4")
        I.take(self.cfg, now=self.cfg.slot_time(DAY, "06:00"))
        self.assertEqual(B.read(acct, DAY)["slots"]["07:00"]["status"], B.READY)   # the default: waits for Approve
        q = self.drop("senator cornered 2.mp4")
        out = I.take(self.cfg, now=self.cfg.slot_time(DAY, "06:00"))
        self.assertIn("REFUSED, a copy of Taken/", out[0])
        self.assertTrue((q / "Refused" / "senator cornered 2 - why.txt").exists())
        self.assertEqual(B.read(acct, DAY)["slots"]["08:45"]["status"], B.OPEN)


class TikTokNowTest(Base):
    """PP-13: Post to TikTok now never sends twice, and a crash never leaves the card on 'posting now'."""

    def test_1_once_at_a_time_and_errors_are_recorded(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        sent = []
        self.patch(D, "tiktok_post", lambda acct, clip, words, slot: sent.append(1) or {"post_id": "v1", "privacy": "SELF_ONLY"})
        with B.edit(self.cfg, self.acct, DAY) as bd:                          # a first click, a moment ago
            bd["slots"]["07:00"].setdefault("outlets", {})["tiktok"] = {"waiting": "posting to TikTok now",
                                                                        "now_since": P._utc(self.cfg.now())}
        self.assertIn("already on its way", P.tiktok_now(self.cfg, self.acct, DAY, "07:00"))
        self.assertEqual(sent, [])
        self.patch(D, "tiktok_post", lambda *a: (_ for _ in ()).throw(ValueError("no JSON in the answer")))
        with B.edit(self.cfg, self.acct, DAY) as bd:                          # that one died 20 minutes ago
            bd["slots"]["07:00"]["outlets"]["tiktok"]["now_since"] = P._utc(self.cfg.now() - timedelta(minutes=20))
        self.assertIn("ValueError", P.tiktok_now(self.cfg, self.acct, DAY, "07:00"))
        rec = self.slot("07:00")["outlets"]["tiktok"]
        self.assertNotIn("waiting", rec)
        self.assertNotIn("now_since", rec)
        self.assertIn("ValueError", rec["last_error"])


class HealthTest(Base):
    """PP-15: post-due says when it broke or stopped."""

    def test_1_a_broken_run_and_a_stopped_timer_are_said(self):
        P.post_due(self.cfg, now=self.at("07:00"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(P.health_words(self.cfg), "")
        self.patch(P, "_pass", lambda *a: (_ for _ in ()).throw(SyntaxError("invalid syntax (direct.py, line 9)")))
        with self.assertRaises(SyntaxError):
            P.post_due(self.cfg, now=self.at("07:10"), posters=self.posters(), uploader=self.uploader())
        self.assertIn("Posting broke at", P.health_words(self.cfg))
        self.assertIn("direct.py", P.health_words(self.cfg))
        P._pass = _REAL_PASS                                                    # fixed: the next run is clean
        P.post_due(self.cfg, now=self.at("07:20"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(P.health_words(self.cfg), "")
        h = json.loads((C.STATE / "post-due.health").read_text())
        h["last_ok"] = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat()
        (C.STATE / "post-due.health").write_text(json.dumps(h))
        self.assertIn("has not run since", P.health_words(self.cfg))


_REAL_PASS = P._pass


class MatchAndDryRunTest(Base):
    """PP-16: a stats match by words alone never stops a slot posting. PP-17: the dry run shows the real file."""

    def test_1_a_sibling_post_matched_by_words_does_not_stop_the_slot(self):
        self.ready("12:15", "14:00")
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["12:15"].setdefault("outlets", {})["facebook_reels"] = {
                "post_id": "R07", "provider": "matched", "platform_id": "R07", "url": "https://fb/R07",
                "published_at": self.at("07:00").isoformat(), "metrics": {"views": 179}}
            bd["slots"]["14:00"].setdefault("outlets", {})["facebook_reels"] = {
                "post_id": "R14", "provider": "matched", "platform_id": "R14", "url": "https://fb/R14",
                "published_at": self.at("14:01").isoformat()}            # posted by hand at its time: it stays
        P.post_due(self.cfg, now=self.at("12:16"), posters=self.posters(), uploader=self.uploader())
        P.post_due(self.cfg, now=self.at("14:30"), posters=self.posters(), uploader=self.uploader())
        fb = self.slot("12:15")["outlets"]["facebook_reels"]
        self.assertEqual((fb["post_id"], fb["provider"]), ("facebook_reels-12:15", "direct"))
        self.assertNotIn("metrics", fb)
        self.assertEqual(self.slot("14:00")["outlets"]["facebook_reels"]["post_id"], "R14")
        self.assertNotIn(("facebook_reels", "14:00"), self.calls)

    def test_2_the_dry_run_names_the_file_and_the_refusal(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        self.acct.outlets["instagram_reels"]["max_seconds"] = 1.5
        out = P.post_due(self.cfg, now=self.at("07:01"), dry_run=True, posters=self.posters(), uploader=self.uploader())
        fb = next(ln for ln in out if "facebook_reels" in ln)
        ig = next(ln for ln in out if "instagram_reels" in ln)
        self.assertIn("would post 0700 clip-0700/01-x.mp4", fb)
        self.assertIn("would NOT post: no Instagram cut", ig)
        self.assertEqual(self.calls, [])


class KeysTest(Base):
    """PP-19: key files are written whole, and one refresh runs at a time."""

    def test_1_atomic_private_writes_and_one_refresh_at_a_time(self):
        p = self.tmp / "keys" / "x" / "k.json"
        D.write_private(p, '{"a": 1}')
        self.assertEqual((p.read_text(), oct(p.stat().st_mode & 0o777)), ('{"a": 1}', "0o600"))
        self.assertEqual([q.name for q in p.parent.iterdir()], ["k.json"])
        D._save(self.acct, "tiktok", {"client_key": "k", "client_secret": "s", "refresh_token": "r0",
                                      "access_token": "a0", "expires_at": 0})
        refreshed = []

        def http(method, url, params=None, form=None, body=None, data=None, headers=None, timeout=0):
            refreshed.append(form["refresh_token"])
            time.sleep(0.3)
            return {"access_token": f"a{len(refreshed)}", "refresh_token": f"r{len(refreshed)}", "expires_in": 86400}
        self.patch(D, "_http", http)
        got = []
        ts = [threading.Thread(target=lambda: got.append(D._tt_token(self.acct))) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual((refreshed, got), (["r0"], ["a1", "a1"]))


class SignedByHandTest(Base):
    """STA-4: a slot signed while a platform was by hand stays by hand after it switches to api (the owner's switch)."""

    def test_1_the_old_note_is_read(self):
        s = {"signoff": {"hash": "h"}, "notes": [
            "2026-10-17T19:53:47+00:00 approved by Andrew",
            "2026-10-17T19:53:47+00:00 scheduled (YouTube and Facebook post by themselves; TikTok and Instagram are posted by hand)"]}
        self.assertEqual(O.handed_by_hand(s), ["tiktok", "instagram_reels"])
        s["notes"].append("2026-10-17T20:00:00+00:00 pulled by Andrew")
        self.assertIsNone(O.handed_by_hand(s))

    def test_2_switched_on_it_stays_by_hand(self):
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertEqual(self.slot("07:00")["handed"]["by_hand"], ["tiktok"])
        self.acct.outlets["tiktok"] = {"mode": "api", "via": "upload-post"}    # TikTok switched on, no "from"
        now = self.at("06:00")
        self.assertEqual(len(P.hand_out_drift(self.cfg, now)), 1)              # doctor says so
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.posters(), uploader=self.uploader())
        self.assertIn(("tiktok", "07:00"), self.calls)                         # switch off: as before
        self.ready("08:45")
        S.approve(self.cfg, self.acct, DAY, ["08:45"], "Andrew")               # signed while TikTok is api
        self.acct.outlets["tiktok"] = {"mode": "export"}
        self.ready("10:30")
        S.approve(self.cfg, self.acct, DAY, ["10:30"], "Andrew")               # signed for TikTok by hand
        self.acct.outlets["tiktok"] = {"mode": "api", "via": "upload-post"}
        self.acct.raw["signed_by_hand_stays"] = True
        self.assertEqual(P.hand_out_drift(self.cfg, now), [])
        self.assertFalse(O.covers_slot(self.acct, "tiktok", self.slot("10:30")))
        self.assertTrue(O.covers_slot(self.acct, "tiktok", self.slot("08:45")))
        P.post_due(self.cfg, now=self.at("10:31"), posters=self.posters(), uploader=self.uploader())
        self.assertNotIn(("tiktok", "10:30"), self.calls)
        O.export(self.cfg, self.acct, DAY, [self.slot("08:45"), self.slot("10:30")], dry_run=False)
        ready = K.drafts_dir(self.cfg, self.acct, DAY) / "Ready to post" / "tiktok"
        self.assertEqual(sorted(p.name for p in ready.glob("*.mp4")), ["1030 clip-1030.mp4"])


class YouTubeListTest(Base):
    """YTH-14: a scheduled upload is dated when it goes public, and an undated item never stops the read."""

    def test_1_publish_at_and_undated_items(self):
        self.connect_youtube()
        self.patch(net, "guard", lambda what: None)
        since = datetime(2031, 5, 1, tzinfo=timezone.utc)

        def http(method, url, params=None, form=None, body=None, data=None, headers=None, timeout=0):
            if url.endswith("/channels"):
                return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
            if url.endswith("/playlistItems"):
                return {"items": [{"contentDetails": {"videoId": "stub"}},
                                  {"contentDetails": {"videoId": "sched", "videoPublishedAt": "2031-05-06T11:28:00Z"}},
                                  {"contentDetails": {"videoId": "old", "videoPublishedAt": "2031-04-01T00:00:00Z"}}],
                        "nextPageToken": "more"}
            if url.endswith("/videos"):
                self.assertEqual(params["id"], "stub,sched")
                return {"items": [
                    {"id": "stub", "snippet": {"title": "s", "publishedAt": "2031-05-06T10:00:00Z"}, "statistics": {}},
                    {"id": "sched", "snippet": {"title": "t", "publishedAt": "2031-05-06T11:28:00Z"}, "statistics": {},
                     "status": {"publishAt": "2031-05-06T17:00:00Z"}}]}
            raise AssertionError(url)
        self.patch(D, "_http", http)
        rows = {r["platform_id"]: r for r in D.yt_list(self.acct, since)}
        self.assertEqual(sorted(rows), ["sched", "stub"])
        self.assertEqual(rows["sched"]["published_at"], "2031-05-06T17:00:00+00:00")


class ReviewFindingsTest(Base):
    """The 2026-10-17 review of these fixes: eight edge cases, one test each (and the review page's hooks)."""

    def upload_then_change(self) -> None:
        """07:00 uploaded to YouTube (vid1), pulled, its clip fixed and approved again: vid1 is set aside."""
        self.ready("07:00")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=self.uploader())
        S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        shutil.copy(self.other, self.slot("07:00")["clip"]["path"])
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")

    def stats(self, during=None):
        from clipfactory import stats as ST
        self.patch(ST, "OUT", self.tmp / "state" / "stats")
        title = ST._slot_text(self.slot("07:00"), "youtube_shorts")
        video = {"platform_id": "vid1", "url": "https://www.youtube.com/shorts/vid1", "text": title,
                 "published_at": self.at("07:00").isoformat(), "views": 5}

        def lister(acct, since):
            if acct.slug != self.acct.slug:
                return []
            if during:
                during()
            return [video]
        return lambda: ST.refresh(self.cfg, listers={"youtube_shorts": lister}, full=False)

    def test_1_the_stats_read_never_ties_a_set_aside_upload_back(self):
        self.upload_then_change()
        read = self.stats()
        read()
        yt = self.slot("07:00")["outlets"]["youtube_shorts"]
        self.assertEqual((yt.get("post_id"), yt["gave_up"], yt["old"]["video_id"]), (None, True, "vid1"))
        S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertEqual(list(self.slot("07:00")["outlets"]["youtube_shorts"]), ["old"])     # kept through Try again
        read()
        self.assertNotIn("post_id", self.slot("07:00")["outlets"]["youtube_shorts"])
        P.post_due(self.cfg, now=self.at("05:50"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["youtube_shorts"]["video_id"], "vid2")

    def test_1b_a_read_under_way_and_a_match_older_code_wrote_never_stop_the_new_clip(self):
        self.ready("07:00")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=self.uploader())

        def change():                 # Pull, fix and Approve while the stats read waits on YouTube
            S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
            shutil.copy(self.other, self.slot("07:00")["clip"]["path"])
            S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.stats(during=change)()
        yt = self.slot("07:00")["outlets"]["youtube_shorts"]
        self.assertEqual((yt.get("post_id"), yt["old"]["video_id"]), (None, "vid1"))
        with B.edit(self.cfg, self.acct, DAY) as bd:          # what the stats read wrote before this fix
            bd["slots"]["07:00"]["outlets"]["youtube_shorts"].update(post_id="vid1", provider="matched",
                                                                    platform_id="vid1", url="https://y/vid1")
        self.assertIn("will be tried again", S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0])
        self.assertEqual(list(self.slot("07:00")["outlets"]["youtube_shorts"]), ["old"])
        P.post_due(self.cfg, now=self.at("05:50"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["youtube_shorts"]["video_id"], "vid2")

    def test_1c_pull_and_approve_never_get_round_making_the_old_one_private(self):
        self.upload_then_change()                                     # vid1 set aside: make it private, Try again
        out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0]
        self.assertIn("YouTube still has the OLD clip (vid1): it goes public at 07:00 unless you make it private in "
                      "Studio: https://studio.youtube.com/video/vid1/edit", out)
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0]
        self.assertIn("YouTube has the OLD clip (vid1)", out)
        self.assertTrue(self.slot("07:00")["outlets"]["youtube_shorts"]["gave_up"])
        P.post_due(self.cfg, now=self.at("05:40"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(len(self.calls), 1)                          # no second upload behind his back
        S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")       # he made it private: Try again
        self.assertIn("cleared", self.slot("07:00")["outlets"]["youtube_shorts"]["old"])
        out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0]
        self.assertNotIn("OLD clip", out)
        self.assertNotIn("OLD clip", S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0])
        P.post_due(self.cfg, now=self.at("05:50"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["youtube_shorts"]["video_id"], "vid2")

    def test_1d_a_third_version_never_ties_the_first_old_upload_back(self):
        self.upload_then_change()                                     # vid1 set aside
        S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")       # made private, Try again: vid2 goes up
        P.post_due(self.cfg, now=self.at("05:40"), posters=self.posters(), uploader=self.uploader())
        S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        clip(Path(self.slot("07:00")["clip"]["path"]), 2.0, "red")    # a third version
        self.assertIn("YouTube has the OLD clip (vid2)", S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0])
        yt = self.slot("07:00")["outlets"]["youtube_shorts"]
        self.assertEqual((yt["old"]["video_id"], yt["old"]["before"]), ("vid2", ["vid1"]))
        from clipfactory import stats as ST
        self.patch(ST, "OUT", self.tmp / "state" / "stats")
        title = ST._slot_text(self.slot("07:00"), "youtube_shorts")
        videos = [{"platform_id": v, "url": f"https://www.youtube.com/shorts/{v}", "text": title,
                   "published_at": self.at("07:00").isoformat(), "views": 1} for v in ("vid1", "vid2")]
        ST.refresh(self.cfg, listers={"youtube_shorts": lambda a, since: videos if a.slug == self.acct.slug else []},
                   full=False)
        self.assertNotIn("post_id", self.slot("07:00")["outlets"]["youtube_shorts"])
        S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.at("05:50"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["youtube_shorts"]["video_id"], "vid3")

    def test_2_pull_says_plainly_how_to_stop_youtube(self):
        self.ready("07:00")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=self.uploader())
        self.patch(self.cfg, "now", lambda: self.at("06:00"))
        out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0]
        self.assertIn("YouTube already has it and makes it public at 07:00. To stop that, make it private in Studio: "
                      "https://studio.youtube.com/video/vid1/edit. To keep it, approve the same clip again", out)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.cfg.now = lambda: self.at("09:00")
        out = S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0]
        self.assertIn("YouTube already has it, public since 07:00. To take it down, make it private in Studio", out)

    def test_3_an_upload_from_before_the_fix_is_dated_back_at_pull(self):
        self.ready("07:00", "08:45")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, None, "Andrew")
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=self.uploader())
        with B.edit(self.cfg, self.acct, DAY) as bd:          # as the boards are tonight: no record of the clip
            for k in ("07:00", "08:45"):
                for key in ("clip_sha", "copy_sha"):
                    bd["slots"][k]["outlets"]["youtube_shorts"].pop(key)
        S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertEqual(self.slot("07:00")["outlets"]["youtube_shorts"]["clip_sha"],
                         self.slot("07:00")["clip"]["sha256"])
        shutil.copy(self.other, self.slot("07:00")["clip"]["path"])
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")[0]
        self.assertIn("YouTube has the OLD clip", out)
        S.pull(self.cfg, self.acct, DAY, ["08:45"], "Andrew")
        self.assertIn("(the same clip)", S.approve(self.cfg, self.acct, DAY, ["08:45"], "Andrew")[0])
        S.pull(self.cfg, self.acct, DAY, ["08:45"], "Andrew")
        with B.edit(self.cfg, self.acct, DAY) as bd:          # pulled before the fix: nothing could date it back
            r = bd["slots"]["08:45"]["outlets"]["youtube_shorts"]
            for key in ("clip_sha", "copy_sha", "shas_from"):
                r.pop(key)
        shutil.copy(self.other, self.slot("08:45")["clip"]["path"])
        out = S.approve(self.cfg, self.acct, DAY, ["08:45"], "Andrew")[0]
        self.assertNotIn("keeps its upload from before (the same clip)", out)
        self.assertIn("If you changed the clip, make that one private in Studio", out)
        self.assertEqual(self.slot("08:45")["outlets"]["youtube_shorts"]["video_id"], "vid2")   # never a second one

    def test_4_a_look_up_that_keeps_failing_lets_try_again_send_it_fresh(self):
        fake = self.meta()
        self.acct.outlets["instagram_reels"]["mode"] = "export"
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.fail_before["fb_finish"] = D.PlatformError("timed out")
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        for t in ("07:12", "07:24"):
            fake.fail_before["fb_status"] = D.PlatformError("HTTP 500 from graph.facebook.com: An unknown error occurred")
            P.post_due(self.cfg, now=self.at(t), posters=self.real_meta_posters(), uploader=self.uploader())
        fb = self.slot("07:00")["outlets"]["facebook_reels"]
        self.assertTrue(fb["gave_up"] and fb["lookup_failed"])
        self.assertIn("Look on Facebook for this clip: if it is not there, press Try again", fb["why"])
        S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")            # Pull still keeps what was sent
        self.assertEqual(list(self.slot("07:00")["outlets"]["facebook_reels"]), ["attempt"])
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"]["outlets"]["facebook_reels"].update(gave_up=True, lookup_failed=True, why="x")
        out = S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertIn("sent fresh", out[0])
        P.post_due(self.cfg, now=self.at("07:40"), posters=self.real_meta_posters(), uploader=self.uploader())
        self.assertEqual((self.slot("07:00")["outlets"]["facebook_reels"]["post_id"], fake.calls.count("fb_start")),
                         ("V2", 2))

    def test_4c_metas_missing_permissions_answer_is_not_gone(self):
        meta = ("HTTP 400 from graph.facebook.com: Unsupported get request. Object with ID 'V1' does not exist, cannot "
                "be loaded due to missing permissions, or does not support this operation")
        self.assertFalse(D._gone(D.PlatformError(meta)))
        self.assertTrue(D._gone(D.PlatformError("HTTP 400 from graph.facebook.com: Object with ID 'V1' does not exist")))
        fake = self.meta()
        self.acct.outlets["instagram_reels"]["mode"] = "export"
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        fake.fail_after["fb_finish"] = D.PlatformError("timed out")          # published; the answer lost
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        fake.fail_before["fb_status"] = D.PlatformError(meta)                # the token lost a permission
        P.post_due(self.cfg, now=self.at("07:12"), posters=self.real_meta_posters(), uploader=self.uploader())
        fb = self.slot("07:00")["outlets"]["facebook_reels"]
        self.assertEqual((fb["tries"], fake.published, fake.calls.count("fb_start")), (2, ["V1"], 1))

    def test_4b_youtube_the_same(self):
        self.ready("07:00")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.patch(net, "guard", lambda what: None)
        self.patch(O, "_yt_access_token", lambda tf: "AT")
        with B.edit(self.cfg, self.acct, DAY) as bd:          # a try whose answer never came
            bd["slots"]["07:00"].setdefault("outlets", {})["youtube_shorts"] = {
                "attempt": {"session": "https://upload.example/s1", "size": 1, "title": "t", "at": "2031-05-06T10:00:00+00:00"},
                "tries": 1, "tried_at": "2031-05-06T10:00:00+00:00"}
        self.patch(O, "_yt_session", lambda access, session, size: (_ for _ in ()).throw(
            RuntimeError("YouTube did not say what became of the last upload (500): backend error")))
        for t in ("05:30", "05:40"):
            P.post_due(self.cfg, now=self.at(t), posters=self.posters(), uploader=O.youtube_upload)
        yt = self.slot("07:00")["outlets"]["youtube_shorts"]
        self.assertTrue(yt["lookup_failed"])
        self.assertIn("Look on YouTube for this clip", yt["why"])
        S.retry(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.assertNotIn("youtube_shorts", self.slot("07:00")["outlets"])

    def test_5_an_early_post_by_hand_on_a_platform_handed_out_by_hand_is_not_cleared(self):
        self.acct.outlets["facebook_reels"] = {"mode": "export"}
        self.ready("12:15")
        S.approve(self.cfg, self.acct, DAY, ["12:15"], "Andrew")
        self.assertIn("facebook_reels", self.slot("12:15")["handed"]["by_hand"])
        self.acct.outlets["facebook_reels"] = {"mode": "api"}           # switched on after it was handed out
        with B.edit(self.cfg, self.acct, DAY) as bd:                   # Andrew posted it by hand at 07:00
            bd["slots"]["12:15"].setdefault("outlets", {})["facebook_reels"] = {
                "post_id": "R07", "provider": "matched", "platform_id": "R07", "url": "https://fb/R07",
                "published_at": self.at("07:00").isoformat()}
        P.post_due(self.cfg, now=self.at("12:16"), posters=self.posters(), uploader=self.uploader())
        self.assertEqual(self.slot("12:15")["outlets"]["facebook_reels"]["post_id"], "R07")
        self.assertNotIn(("facebook_reels", "12:15"), self.calls)

    def test_6_only_the_exact_name_with_2_to_9_is_an_icloud_copy(self):
        from clipfactory import inbox as I
        d = self.tmp / "queue" / "x"
        (d / "Taken").mkdir(parents=True)
        for n in ("2031-05-06 0700 my clip.mp4", "2031-05-06 0845 rally.mp4", "2031-05-06 1030 x.mov"):
            (d / "Taken" / n).write_text("x")
        got = {n: I._copy_of_taken(d, d / n) for n in
               ("clip 2.mp4", "my clip 2.mp4", "rally 2026.mp4", "rally 2.mp4", "Part 10.mp4", "x 2.MOV", "rally 10.mp4")}
        self.assertEqual(got, {"clip 2.mp4": "", "my clip 2.mp4": "2031-05-06 0700 my clip.mp4", "rally 2026.mp4": "",
                               "rally 2.mp4": "2031-05-06 0845 rally.mp4", "Part 10.mp4": "",
                               "x 2.MOV": "2031-05-06 1030 x.mov", "rally 10.mp4": ""})

    def test_7_doctor_words_are_the_owners_call_and_true(self):
        self.acct.outlets["instagram_reels"] = {"mode": "export"}
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        self.acct.outlets["instagram_reels"] = {"mode": "api"}
        now = self.at("06:00")
        [(line, fix)] = P.drift_items(self.cfg, now)
        self.assertIn("Instagram was signed to be posted by hand, but now it posts by itself", line)
        self.assertIn("it posts by itself at 07:00, so do not also post it by hand", fix)
        self.acct.outlets["instagram_reels"]["max_seconds"] = 1.5      # the Patel case: no Instagram cut
        [(line, fix)] = P.drift_items(self.cfg, now)
        self.assertIn("as signed it would NOT post (no Instagram cut", line)
        s = self.slot("07:00")                                          # the clip itself: Ready to post is rebuilt
        self.assertIn(f"it needs its own Instagram cut first; or post the clip by hand if Instagram takes its length "
                      f"there: {s['clip']['path']} with the words in {s['copy']['instagram_reels']['path']}", fix)
        self.assertNotIn("approve it again", fix)
        cut = Path(s["source_folder"]) / "instagram.mp4"
        shutil.copy(self.master, cut)
        with B.edit(self.cfg, self.acct, DAY) as bd:
            B.note(bd["slots"]["07:00"], "a new Instagram cut arrived after signing; Pull and approve again to take it")
        [(line, fix)] = P.drift_items(self.cfg, now)
        self.assertIn("a new Instagram cut is waiting", line)
        self.assertIn("Pull it, watch the new Instagram cut, approve it again", fix)
        self.assertIn(f"or post that cut by hand: {cut} with the words in ", fix)
        self.assertEqual(P.hand_out_drift(self.cfg, now), [line])
        for text in (fix, P.OWNERS_CALL):
            self.assertTrue(text.startswith("Andrew's call; a bot reports this and changes nothing"))
            self.assertNotIn("accounts.json", text)
            self.assertNotIn("factory.py post-due", text)

    def test_8_a_reel_an_earlier_signature_made_keeps_its_flag(self):
        fake = self.meta()
        self.acct.outlets["instagram_reels"]["mode"] = "export"
        self.ready("07:00")
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        first = self.slot("07:00")["clip"]["sha256"]
        fake.fail_after["fb_finish"] = D.PlatformError("timed out")          # Facebook published; no answer
        P.post_due(self.cfg, now=self.at("07:01"), posters=self.real_meta_posters(), uploader=self.uploader())
        self.assertEqual(self.slot("07:00")["outlets"]["facebook_reels"]["attempt"]["clip_sha"], first)
        S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        shutil.copy(self.other, self.slot("07:00")["clip"]["path"])
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        P.post_due(self.cfg, now=self.at("07:15"), posters=self.real_meta_posters(), uploader=self.uploader())
        fb = self.slot("07:00")["outlets"]["facebook_reels"]
        self.assertEqual((fb["post_id"], fb["clip_sha"]), ("V1", first))     # what that try sent, not the clip now
        self.assertIn("version from before", fb["flag"])
        S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        out = S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")      # the new clip again, unchanged
        self.assertIn("Facebook has the version from before", out[0])
        self.assertIn("version from before", P.flag_words(self.slot("07:00")))
        self.assertEqual(fake.calls.count("fb_start"), 1)

    def test_9_the_review_page_shows_the_red_line_the_flag_and_the_tiktok_guard(self):
        from clipfactory import review as R
        self.ready("07:00")
        self.connect_youtube()
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        now = self.cfg.now()
        self.assertNotIn("health", R._needs(self.cfg, now)["head"])
        self.patch(P, "_pass", lambda *a: (_ for _ in ()).throw(SyntaxError("invalid syntax (direct.py, line 9)")))
        with self.assertRaises(SyntaxError):
            P.post_due(self.cfg, now=self.at("05:20"), posters=self.posters(), uploader=self.uploader())
        head = R._needs(self.cfg, now)["head"]
        self.assertIn('class="alert health"', head)
        self.assertIn("Posting broke at", head)
        P._pass = _REAL_PASS
        up = self.uploader(then=lambda: S.pull(self.cfg, self.acct, DAY, ["07:00"], "Andrew"))
        P.post_due(self.cfg, now=self.at("05:30"), posters=self.posters(), uploader=up)
        self.assertNotIn('class="alert health"', R._needs(self.cfg, now)["head"])        # a clean run clears it
        bottom = R._bottom(self.cfg, self.acct, DAY, self.slot("07:00"), now)
        self.assertIn("YouTube got this clip while you pulled it", bottom)
        S.approve(self.cfg, self.acct, DAY, ["07:00"], "Andrew")
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"].setdefault("outlets", {})["tiktok"] = {"waiting": "posting to TikTok now",
                                                                        "now_since": P._utc(now)}
        ok, said = R._tt_act(self.cfg, self.acct, DAY, "tt-now", "07:00", {}, now)
        self.assertEqual((ok, said), (False, "It is already on its way to TikTok; this card says when it is up."))


if __name__ == "__main__":
    unittest.main()
