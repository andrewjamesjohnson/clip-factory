import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Cards and stories from real footage (2026-10-20): the cards board, its hash and Approve, a signature voided by a
# change; the Politics quote card only from a line found word for word and a speaker a person verified; every file under
# state/; the engine run as a subprocess (a fake one, and the real one when this Mac's engine Python has Pillow); the
# Instagram MP4 (real ffmpeg when it is here); story reposts under the signed rule (60 seconds at the most); Instagram's
# publishing room; expiry; a quote card waiting on its clip; each Graph route a card or a story takes; nothing at night;
# off means off; a card's picture only a clean frame of the clip's source (never our delivered clip, a hard link of
# it, its cover or its folders); every kind centred on the canvas and clear of a story's bars (the real engine's
# pixels, measured); the countdown never on anyone's footage; the clip of the day on a game frame over a web page
# when its span has one. Throwaway folders, fake platforms, no network.

import json
import shutil
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import (Clock, FakeEngine, FakeGraph, cfg_in_tmp, patch_http, slot_posted,  # noqa: E402
                          slot_ready, write_tokens)

from clipfactory import board as B              # noqa: E402
from clipfactory import capabilities as CAP     # noqa: E402
from clipfactory import cards as CD             # noqa: E402
from clipfactory import cards_api as API        # noqa: E402
from clipfactory import config as C             # noqa: E402
from clipfactory import outward as OW           # noqa: E402
from clipfactory import registry as REG         # noqa: E402
from clipfactory import social as SOC           # noqa: E402
from clipfactory import store                   # noqa: E402
from clipfactory import switches as SW          # noqa: E402

DAY = date(2026, 11, 2)              # a Monday, 17 days before GTA 6
JOB = "ABC Test Ross 11.01.26"
QUOTE = "We will cut the rate by 25 percent this year."
SRT = ("1\n00:00:01,000 --> 00:00:02,000\nThank you all.\n\n"
       "2\n00:00:10,000 --> 00:00:11,500\nWe will cut the\n\n"
       "3\n00:00:11,500 --> 00:00:13,000\nrate by 25 percent\n\n"
       "4\n00:00:13,000 --> 00:00:14,000\nthis year. And then\n")
ROSS = {"id": "ross", "name": "Pat Ross", "title": "TREASURY SECRETARY",
        "verified": "Andrew, in Clip Review's Casts (2026-11-01 07:10): looked at the stills, this is Pat Ross"}
LEE = {"id": "lee", "name": "Sam Lee", "title": "SENATOR",
       "verified": "Andrew, in Clip Review's Casts (2026-11-01 07:12): this is Sam Lee"}
META_ALL = ["instagram_basic", "instagram_manage_comments", "pages_read_engagement", "pages_read_user_content",
            "pages_manage_engagement", "instagram_content_publish", "pages_manage_posts"]
PAGE, IG = "1000001", "17841400000000001"
ENGINE_PY = "/usr/bin/python3"


def _engine_has_pil() -> bool:
    try:
        r = subprocess.run([ENGINE_PY, "-c", "import PIL, fontTools, numpy"], capture_output=True, timeout=20)
        return r.returncode == 0 and all(os.path.exists(f) for f in CD.FONTS)
    except (OSError, subprocess.SubprocessError):
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        self.eng = FakeEngine.script(self.tmp / "engine")
        for p in (mock.patch.object(CD, "ENGINE", self.eng),
                  mock.patch.dict(os.environ, {"CLIP_ENGINE_PYTHON": sys.executable}),
                  mock.patch.object(CD, "_to_mp4", self.fake_mp4), mock.patch.object(CD, "_grab", self.fake_grab),
                  mock.patch.object(CD, "_luma_probe", self.fake_probe),
                  mock.patch.object(API, "_sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.grabs = []
        self.probes = []                 # (source, second) of each probe game_second made
        self.probe_px = {}               # round(second, 2) -> the grey bytes that probe sees (none: unreadable)
        OW.RNG.seed(7)
        self.clock = Clock(self.cfg.slot_time(DAY, "06:40")).patch(self, self.cfg)

    # stand-ins for ffmpeg (the real ones are tested below when ffmpeg is here)
    def fake_mp4(self, jpg, mp4, timeout=120):
        Path(mp4).write_bytes(b"\x00\x00\x00\x18ftypmp42" + Path(jpg).read_bytes()[:16])

    def fake_grab(self, src, sec, out, timeout=60):
        self.grabs.append((str(src), sec))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"\xff\xd8\xff\xe0grab\xff\xd9")
        return True

    def fake_probe(self, src, sec, timeout=30):
        self.probes.append((str(src), round(sec, 2)))
        return self.probe_px.get(round(sec, 2))

    def at(self, hhmm: str, day: date = DAY) -> datetime:
        return self.clock.at(hhmm, day)

    def set(self, acct, modes=None, settings=None):
        """The account's switches written straight to its file (a test is not held to the go-live checklist)."""
        p = SW.file_path(acct)
        raw = store.read(p, {}) or {}
        raw.setdefault("modes", {}).update(modes or {})
        raw.setdefault("settings", {}).update(settings or {})
        raw["v"] = 1
        store.write(p, raw)

    def grant(self, acct):
        CAP.save_scopes(acct, "meta", META_ALL, "connect")
        write_tokens(acct, page_id=PAGE, ig_user_id=IG)

    def politics_clip(self, k="07:00", people=(ROSS,), quote=QUOTE, srt=SRT, plates=None, questions=None,
                    cast=True, still=True):
        """A READY Politics clip with its job on disk: slate (the quote), project, cast, plates, a cast check still,
        the source's cues, and the delivered captions beside the clip."""
        s = slot_ready(self, self.politics, DAY, k)[k]
        jd = self.politics.account_dir / "Jobs" / JOB
        (jd / "clips").mkdir(parents=True, exist_ok=True)
        (jd / "castcheck").mkdir(parents=True, exist_ok=True)
        work = self.tmp / "clipwork" / "abc-ross"
        work.mkdir(parents=True, exist_ok=True)
        (jd / "slate.json").write_text(json.dumps({"clips": [{"slug": s["slug"], "quote": quote,
                                                               "questions": questions or []}]}))
        (jd / "project.json").write_text(json.dumps({"source": str(work / "master.mp4"), "source_channel": "ABC News",
                                                     "source_date": "NOVEMBER 1 2026"}))
        if cast:
            (jd / "cast.json").write_text(json.dumps({"people": list(people)}))
        if plates is not None:
            (jd / "clips" / f"01-{s['slug']}.cards.json").write_text(json.dumps({"cards": plates}))
        if still:
            (jd / "castcheck" / "ross-120.0.jpg").write_bytes(b"\xff\xd8\xff\xe0still\xff\xd9")
        (work / "cues.json").write_text(json.dumps([[100.0, 104.0, "Thank you all."],
                                                    [118.0, 121.5, "We will cut the rate"],
                                                    [121.5, 124.0, "by 25 percent this year."]]))
        Path(s["clip"]["path"]).with_suffix(".srt").write_text(srt)
        with B.edit(self.cfg, self.politics, DAY) as bd:
            bd["slots"][k]["cast_check"] = {"job": JOB}
            bd["slots"][k]["host"] = "ABC News"
        return B.read(self.politics, DAY)["slots"][k]

    def sign_clip(self, acct, day, k):
        with B.edit(self.cfg, acct, day) as bd:
            s = bd["slots"][k]
            s["signoff"] = {"by": "Andrew", "at": B.utcnow(), "hash": B.content_hash(s)}
            s["status"] = B.SCHEDULED

    def posted(self, acct, day, *slots, views=None, published=None):
        got = slot_posted(self, acct, day, *slots, published=published)
        if views:
            with B.edit(self.cfg, acct, day) as bd:
                for k, v in views.items():
                    for o in bd["slots"][k]["outlets"].values():
                        o["metrics"] = {"views": v}
        return got

    def make(self, acct, day=DAY, hhmm="06:40", **kw):
        return CD.make(self.cfg, acct, day, self.at(hhmm, day), **kw)

    def files_under_state(self, card):
        for f in (card.get("files") or {}).get("images", []) + (card.get("files") or {}).get("videos", []):
            p = Path(f["path"]).resolve()
            self.assertIn(C.STATE.resolve(), p.parents, p)


# ---------------------------------------------------------------------------------------------- the quote card
class QuoteCardTest(Base):
    def setUp(self):
        super().setUp()
        self.set(self.politics, {"cards.make": "on"}, {"cards.targets": ["facebook_story", "instagram_story"]})

    def test_1_a_verified_single_speaker_gets_the_line_word_for_word(self):
        self.politics_clip()
        lines = self.make(self.politics, kinds=["quote"])
        card = CD.get_card(self.politics, DAY, "quote-0700")
        self.assertIsNotNone(card, lines)
        self.assertEqual(card["status"], "ready")
        self.assertEqual(card["words"]["quote"], QUOTE)
        self.assertEqual((card["words"]["name"], card["words"]["title"]), ("Pat Ross", "TREASURY SECRETARY"))
        self.assertEqual(card["words"]["source"], "ABC News · NOVEMBER 1 2026")
        self.assertEqual(card["source"]["quote"]["clip_s"], [10.0, 14.0])
        self.assertEqual(card["source"]["quote"]["source_s"], [118.0, 124.0])
        self.assertEqual(card["source"]["line"], "speaker verified by Andrew 11-01")
        self.assertEqual(card["after"], {"slot": "07:00", "outlet": "instagram_reels", "minutes": [60, 90]})
        self.assertEqual(card["targets"], ["facebook_story", "instagram_story"])
        self.assertEqual(len(card["files"]["images"]), 1)
        self.assertEqual(len(card["files"]["videos"]), 1)            # the Instagram copy
        self.files_under_state(card)
        call = FakeEngine.calls(self.eng.parent)[-1]
        self.assertEqual(call["argv"][0], str(CD.card_dir(self.politics, DAY, "quote-0700") / "r1" / "spec.json"))
        slide = call["spec"]["slides"][0]
        self.assertEqual((slide["layout"], slide["quote"], slide["name"]), ("quote", QUOTE, "Pat Ross"))
        self.assertEqual(Path(slide["still"]).name, "still-1.jpg")     # the cast check still, copied into state
        self.assertIn(C.STATE.resolve(), Path(slide["still"]).resolve().parents)
        self.assertEqual(call["spec"]["look"], "politics")
        self.assertEqual(self.grabs, [])                                # no master on disk: no grab

    def test_2_the_master_gives_the_frame_at_the_line(self):
        self.politics_clip()
        work = self.tmp / "clipwork" / "abc-ross"
        (work / "master.mp4").write_bytes(b"fake master")
        self.make(self.politics, kinds=["quote"])
        self.assertEqual(len(self.grabs), 1)
        src, sec = self.grabs[0]
        self.assertEqual(src, str(work / "master.mp4"))
        self.assertAlmostEqual(sec, 119.0)

    def no_card(self, why_part, **kw):
        self.politics_clip(**kw)
        lines = self.make(self.politics, kinds=["quote"])
        self.assertIsNone(CD.get_card(self.politics, DAY, "quote-0700"))
        self.assertTrue(any(why_part in ln for ln in lines), lines)

    def test_3_refused_for_two_people_and_no_plate(self):
        self.no_card("no speaker verified by a person", people=(ROSS, LEE))

    def test_4_refused_for_a_provisional_cast(self):
        prov = dict(ROSS, verified="", provisional={"named_as": "Pat Ross", "by": "politics bot"})
        self.no_card("no speaker verified by a person", people=(prov,))

    def test_5_refused_for_a_missing_cast(self):
        self.no_card("no speaker verified by a person", cast=False)

    def test_6_refused_when_a_bot_wrote_verified(self):
        self.no_card("no speaker verified by a person", people=(dict(ROSS, verified="Claude checked the stills"),))

    def test_7_refused_for_a_line_not_in_the_captions(self):
        self.no_card("not in the delivered captions word for word",
                     srt=SRT.replace("rate by 25 percent", "rates by 25 percent"))

    def test_8_refused_inside_a_question(self):
        self.no_card("inside a question", questions=[[117.0, 125.0]])

    def test_9_two_people_but_the_line_inside_his_plate(self):
        plate = {"who": "ross", "name": "Pat Ross", "at": 9.6, "hold": 4.6, "end": 14.2}
        self.politics_clip(people=(ROSS, LEE), plates=[plate])
        self.make(self.politics, kinds=["quote"])
        card = CD.get_card(self.politics, DAY, "quote-0700")
        self.assertEqual(card["words"]["name"], "Pat Ross")

    def test_10_no_clean_frame_no_card(self):
        self.no_card("no clean frame", still=False)


# ---------------------------------------------------------------------------------------------- signing
class SigningTest(Base):
    def setUp(self):
        super().setUp()
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["countdown"]})
        self.make(self.gta)
        self.card = CD.get_card(self.gta, DAY, "countdown-0900")

    def test_1_the_board_and_its_hash(self):
        c = self.card
        self.assertEqual((c["kind"], c["status"], c["targets"]), ("countdown", "ready", ["facebook_story"]))
        self.assertEqual(c["words"], {"days": 17, "date": "November 19, 2026"})
        self.assertEqual(c["at"], self.cfg.slot_time(DAY, "09:00").astimezone(timezone.utc).isoformat())
        bd = CD.read_board(self.gta, DAY)
        self.assertEqual((bd["account"], bd["date"]), ("gaming", DAY.isoformat()))
        self.assertIn("made", bd)
        h = CD.card_hash(c)
        self.assertEqual(h, CD.card_hash(json.loads(json.dumps(c))))
        for change in ({"at": "2026-11-02T17:00:00+00:00"}, {"targets": ["instagram_story"]},
                       {"words": {"days": 16}}):
            self.assertNotEqual(CD.card_hash(dict(c, **change)), h, change)
        spec = FakeEngine.calls(self.eng.parent)[-1]["spec"]
        self.assertEqual(spec["slides"][0], {"layout": "countdown", "days": 17, "date": "November 19, 2026"})
        self.assertEqual((spec["look"], spec["size"]), ("gta", [1080, 1920]))
        self.files_under_state(c)
        self.assertEqual(c["files"]["videos"], [])                       # no Instagram target: no MP4

    def test_2_approve_signs_only_what_the_page_showed(self):
        with self.assertRaises(SystemExit) as ex:
            CD.approve(self.cfg, self.gta, DAY, "countdown-0900", "f00", "Andrew", self.at("07:00"))
        self.assertIn("changed after the page showed it", str(ex.exception))
        words = CD.approve(self.cfg, self.gta, DAY, "countdown-0900", CD.card_hash(self.card), "Andrew",
                           self.at("07:00"))
        self.assertIn("Signed: GTA 6 countdown at 9:00 AM to Facebook story", words)
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual((c["status"], c["signoff"]["by"], c["signoff"]["hash"]), ("approved", "Andrew",
                                                                                 CD.card_hash(c)))
        self.assertEqual(CD.signed_ok(c), "")

    def sign(self):
        CD.approve(self.cfg, self.gta, DAY, "countdown-0900", CD.card_hash(self.card), "Andrew", self.at("07:00"))

    def test_3_make_again_voids_the_signature(self):
        self.sign()
        CD.remake(self.cfg, self.gta, DAY, "countdown-0900", "Andrew", self.at("07:05"))
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual((c["status"], c["signoff"]), ("ready", None))
        self.assertIn("made again by Andrew", c["notes"][-1])

    def test_4_a_file_changed_after_signing_is_never_sent(self):
        self.sign()
        Path(self.card["files"]["images"][0]["path"]).write_bytes(b"\xff\xd8other\xff\xd9")
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertIn("changed after it was made", CD.signed_ok(c))
        self.set(self.gta, {"cards.post": "practice"})
        lines = CD._advance(self.cfg, self.gta, DAY, self.at("09:00"))
        self.assertTrue(any("not sent" in ln for ln in lines), lines)
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "failed")
        self.assertEqual(OW.due(self.cfg, self.gta, self.at("09:01"), runner="studio"), [])

    def test_5_a_new_time_voids_the_signature(self):
        self.sign()
        CD.set_time(self.cfg, self.gta, DAY, "countdown-0900", "10:15", "Andrew", self.at("07:10"))
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual((c["status"], c["signoff"]), ("ready", None))
        self.assertEqual(c["at"], self.cfg.slot_time(DAY, "10:15").astimezone(timezone.utc).isoformat())
        with self.assertRaises(SystemExit):
            CD.set_time(self.cfg, self.gta, DAY, "countdown-0900", "23:45", "Andrew")

    def test_6_decline_keeps_its_reasons(self):
        with self.assertRaises(SystemExit):
            CD.decline(self.cfg, self.gta, DAY, "countdown-0900", ["weak"], "", "Andrew")
        CD.decline(self.cfg, self.gta, DAY, "countdown-0900", ["looks_off", "wrong_time"], "too busy", "Andrew")
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(c["status"], "declined")
        self.assertEqual(c["declined"]["reasons"], ["looks_off", "wrong_time"])
        self.assertIn("(Wrong time, Looks off): too busy", c["notes"][-1])

    def test_7_unsigned_cards_expire_two_hours_past_their_time(self):
        CD._advance(self.cfg, self.gta, DAY, self.at("10:59"))
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "ready")
        lines = CD._advance(self.cfg, self.gta, DAY, self.at("11:01"))
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "expired")
        self.assertIn("expired", lines[0])

    def test_8_made_once_a_day(self):
        n = len(FakeEngine.calls(self.eng.parent))
        self.make(self.gta, hhmm="09:30")
        self.assertEqual(len(FakeEngine.calls(self.eng.parent)), n)


# ---------------------------------------------------------------------------------------------- the other kinds
class KindsTest(Base):
    def test_1_docket_needs_three_posted_clips(self):
        self.set(self.politics, {"cards.make": "on"}, {"cards.kinds": ["docket"],
                                                    "cards.targets": ["facebook_feed", "instagram_carousel"]})
        y = DAY - timedelta(days=1)
        self.posted(self.politics, y, "07:00", "08:45")
        lines = self.make(self.politics)
        self.assertIsNone(CD.get_card(self.politics, DAY, "docket-1200"))
        self.assertTrue(any("3 at the least" in ln for ln in lines), lines)
        self.posted(self.politics, y, "10:30")
        self.make(self.politics, again=True)
        c = CD.get_card(self.politics, DAY, "docket-1200")
        self.assertEqual(len(c["files"]["images"]), 4)                  # the cover and three clips
        self.assertEqual(len(c["files"]["videos"]), 4)
        self.assertEqual([i["title"] for i in c["words"]["items"]], ["Clip at 07:00", "Clip at 08:45",
                                                                     "Clip at 10:30"])
        spec = FakeEngine.calls(self.eng.parent)[-1]["spec"]
        self.assertEqual(spec["size"], [1080, 1350])
        self.assertEqual(spec["slides"][0]["layout"], "docket_cover")
        self.assertEqual(spec["slides"][0]["figure"], "3")

    def test_2_week_best_on_mondays_by_views(self):
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["week_best"],
                                                 "cards.targets": ["instagram_carousel"]})
        for i, (k, v) in enumerate((("07:00", 500), ("08:45", 9000), ("10:30", 40))):
            self.posted(self.gta, DAY - timedelta(days=i + 1), k, views={k: v})
        self.make(self.gta)
        c = CD.get_card(self.gta, DAY, "week_best-1900")
        self.assertEqual([i["views"] for i in c["words"]["items"]], [27000, 1500, 120])    # three platforms each
        spec = FakeEngine.calls(self.eng.parent)[-1]["spec"]
        self.assertEqual(spec["slides"][1]["rank"], "#1")
        self.assertNotIn("still", spec["slides"][1])                   # GTA week's best: words on the kit's flat ink
        self.assertEqual(CD.build_week_best(self.cfg, self.gta, DAY + timedelta(days=1), self.at("07:00")), [])

    def test_3_countdown_stops_after_launch(self):
        self.assertEqual(CD.build_countdown(self.cfg, self.gta, CD.LAUNCH, self.at("07:00"))[0][0]["words"]["days"],
                         0)
        self.assertEqual(CD.build_countdown(self.cfg, self.gta, CD.LAUNCH + timedelta(days=1), self.at("07:00")),
                         [])

    def test_4_clip_of_the_day_is_yesterdays_best_never_on_our_own_frame(self):
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["clip_of_day"]})
        y = DAY - timedelta(days=1)
        self.posted(self.gta, y, "07:00", "08:45", views={"07:00": 10, "08:45": 99})
        with B.edit(self.cfg, self.gta, y) as bd:
            bd["slots"]["08:45"]["host"] = "MrBossFTW"
        self.make(self.gta)
        c = CD.get_card(self.gta, DAY, "clip_of_day-2030")
        self.assertEqual(c["words"]["hook"], "Clip at 08:45")
        self.assertEqual(c["words"]["credit"], "From MrBossFTW")
        self.assertEqual(self.grabs, [])               # no master on this Mac: no frame at all, never our clip's
        self.assertEqual(c["source"]["art"], "ink")
        self.assertEqual(FakeEngine.calls(self.eng.parent)[-1]["spec"]["slides"][0]["still"], "")


# ---------------------------------------------------------------------------------------------- clean frames only
class CleanFrameTest(Base):
    """2026-10-20: a card's picture is a clean frame of the footage the clip was cut from (its job's master at the
    clip's moment, or a cast check still pulled from it), never our delivered clip, its cuts or its cover: those carry
    our burned-in captions and hook ("CRASHED", "TO DESCRIBE THAT" on the first clip of the day)."""

    def gta_best(self, slate=None, source=None, pick_span=None):
        """Yesterday's 08:45 GTA clip as the day's most watched, its cover beside it, its job's slate and project in
        the account's Jobs/<job>/_project (the throwaway folder, never the real skill's work/). The slot."""
        y = DAY - timedelta(days=1)
        self.posted(self.gta, y, "07:00", "08:45", views={"07:00": 5, "08:45": 50})
        s = B.read(self.gta, y)["slots"]["08:45"]
        self.job = f"cards-test-{os.getpid()}-{self.id().rsplit('.', 1)[-1][:20]}"
        self.pj = self.gta.account_dir / "Jobs" / self.job / "_project"
        self.pj.mkdir(parents=True, exist_ok=True)
        entry = dict({"slug": s["slug"]}, **({"start": 100.0, "end": 140.0} if slate is None else slate))
        (self.pj / "slate.json").write_text(json.dumps({"clips": [entry]}))
        (self.pj / "project.json").write_text(json.dumps({"source": str(source or "")}))
        cover = Path(s["clip"]["path"]).with_name("01-x.jpg")
        cover.write_bytes(b"\xff\xd8\xff\xe0cover: our hook and captions\xff\xd9")
        with B.edit(self.cfg, self.gta, y) as bd:
            bd["slots"]["08:45"].update(job=self.job, host="MrBossFTW", cover=str(cover))
            if pick_span:
                bd["slots"]["08:45"]["pick_span"] = pick_span
        return B.read(self.gta, y)["slots"]["08:45"]

    def source(self, name="master.mp4") -> Path:
        p = self.tmp / "sources" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"the creator's own footage")
        return p

    def clip_of_day(self):
        card, slides, _ = CD.build_clip_of_day(self.cfg, self.gta, DAY, self.at("07:00"))[0]
        return card, slides[0]

    def test_1_the_clip_of_the_day_is_its_master_at_the_clips_moment(self):
        master = self.source()
        self.gta_best(source=master)
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["clip_of_day"]})
        self.make(self.gta)
        c = CD.get_card(self.gta, DAY, "clip_of_day-2030")
        self.assertEqual(self.grabs, [(str(master), 116.0)])                 # 40% into 100..140
        slide = FakeEngine.calls(self.eng.parent)[-1]["spec"]["slides"][0]
        self.assertEqual(Path(slide["still"]).read_bytes(), b"\xff\xd8\xff\xe0grab\xff\xd9")
        self.assertIn(C.STATE.resolve(), Path(slide["still"]).resolve().parents)
        self.assertEqual(c["source"]["art"], "master")

    def test_2_never_the_delivered_clip_its_hard_link_its_cover_or_its_folders(self):
        s = self.gta_best()
        clip = Path(s["clip"]["path"])
        linked = self.tmp / "sources" / "renamed-master.mp4"            # our clip under another name (same inode)
        linked.parent.mkdir(parents=True, exist_ok=True)
        os.link(clip, linked)
        in_drafts = clip.with_name("master.mp4")                         # anything in the slot's drafts folder
        in_drafts.write_bytes(b"x")
        rendered = self.gta.account_dir / "Jobs" / "some-job" / "clips" / "01-a-clip.mp4"   # a rendered clip
        rendered.parent.mkdir(parents=True, exist_ok=True)
        rendered.write_bytes(b"x")
        for src in (clip, linked, in_drafts, Path(s["cover"]), rendered,
                    Path(s["source_folder"]) / clip.name if s.get("source_folder") else clip):
            with self.subTest(src=str(src)):
                self.assertTrue(CD.is_ours(self.gta, B.read(self.gta, DAY - timedelta(days=1))["slots"]["08:45"],
                                           src))
                (self.pj / "project.json").write_text(json.dumps({"source": str(src)}))
                self.grabs.clear()
                card, slide = self.clip_of_day()
                self.assertEqual(self.grabs, [])
                self.assertEqual(slide["still"], "")
                self.assertEqual(card["source"]["art"], "ink")
        self.assertFalse(CD.is_ours(self.gta, s, self.source()))

    def test_3_no_master_means_no_picture_never_our_clip_or_cover(self):
        s = self.gta_best()
        card, slide = self.clip_of_day()
        self.assertEqual((self.grabs, slide["still"], card["source"]["art"]), ([], "", "ink"))
        self.assertNotIn(Path(s["clip"]["path"]).name, json.dumps(card))

    def test_4_the_pick_span_when_the_slate_has_none(self):
        master = self.source()
        self.gta_best(slate={}, source=master, pick_span=[50.0, 60.0])
        self.clip_of_day()
        self.assertEqual(self.grabs, [(str(master), 54.0)])

    def test_4b_the_clip_of_the_day_prefers_a_game_frame_to_a_web_page(self):
        master = self.source()
        self.gta_best(source=master)                                     # span 100..140
        web = bytes(([235] * 80 + [120] * 80) * 90)                     # flat fills: a page, a browser's chrome
        game = bytes((i * 37 + (i // 160) * 11) % 200 + 30 for i in range(160 * 90))   # texture everywhere
        for f in CD.PROBE_AT:
            self.probe_px[round(100 + 40 * f, 2)] = web
        self.probe_px[128.0] = game                                     # 70% in: the game
        self.clip_of_day()
        self.assertEqual(self.grabs, [(str(master), 128.0)])
        self.assertEqual({p[0] for p in self.probes}, {str(master)})   # only the master is ever probed
        self.assertEqual(len(self.probes), len(CD.PROBE_AT))

    def test_4c_when_unsure_it_keeps_the_40_percent_frame(self):
        master = self.source()
        self.gta_best(source=master)
        web = bytes(([235] * 80 + [120] * 80) * 90)
        a_bit_less = bytes(([235] * 80 + [120] * 70 + [30, 200] * 5) * 90)   # 6% less flat: not clearly better
        for f in CD.PROBE_AT:
            self.probe_px[round(100 + 40 * f, 2)] = web
        self.probe_px[128.0] = a_bit_less
        self.clip_of_day()
        self.assertEqual(self.grabs, [(str(master), 116.0)])
        self.probe_px.clear()                                            # nothing readable: 40% as well
        self.grabs.clear()
        self.clip_of_day()
        self.assertEqual(self.grabs, [(str(master), 116.0)])

    def test_4d_the_flat_ui_measure(self):
        n = CD.PROBE_W * CD.PROBE_H
        self.assertAlmostEqual(CD.chrome_share(bytes([180]) * n), 1.0)                        # one flat fill
        self.assertIsNone(CD.chrome_share(bytes([5]) * n))                                    # a black fade
        self.assertEqual(CD.chrome_share(bytes((i * 37) % 200 + 30 for i in range(n))), 0.0)  # texture
        self.assertEqual(CD.chrome_share(bytes([20, 60] * (n // 2))), 0.0)                    # no long runs
        self.assertIsNone(CD.chrome_share(b""))

    def test_5_the_countdown_never_falls_back_to_our_clip(self):
        self.gta_best()
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["countdown"]})
        self.make(self.gta)
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(self.grabs, [])
        self.assertEqual(c["source"]["art"], "ink")
        self.assertNotIn("still", FakeEngine.calls(self.eng.parent)[-1]["spec"]["slides"][0])

    def test_6_the_politics_carousel_takes_clean_frames_never_the_cover(self):
        y = DAY - timedelta(days=1)
        self.posted(self.politics, y, "07:00", "08:45", "10:30")
        slots = {k: B.read(self.politics, y)["slots"][k] for k in ("07:00", "08:45", "10:30")}
        jd = self.politics.account_dir / "Jobs" / JOB
        (jd / "castcheck").mkdir(parents=True, exist_ok=True)
        clean = b"\xff\xd8\xff\xe0a clean cast check still\xff\xd9"
        (jd / "castcheck" / "ross-120.0.jpg").write_bytes(clean)
        (jd / "slate.json").write_text(json.dumps({"clips": [{"slug": slots[k]["slug"], "start": 100.0, "end": 140.0}
                                                             for k in slots]}))
        master = self.tmp / "clipwork" / "abc-ross" / "master.mp4"
        (jd / "project.json").write_text(json.dumps({"source": str(master)}))
        with B.edit(self.cfg, self.politics, y) as bd:
            for k in slots:
                s = bd["slots"][k]
                cover = Path(s["clip"]["path"]).with_name("01-x.jpg")
                cover.write_bytes(b"\xff\xd8\xff\xe0cover: masthead and captions\xff\xd9")
                s.update(cast_check={"job": JOB}, cover=str(cover))
        self.set(self.politics, {"cards.make": "on"}, {"cards.kinds": ["docket"], "cards.targets": ["facebook_feed"]})

        def stills():
            return [x["still"] for x in FakeEngine.calls(self.eng.parent)[-1]["spec"]["slides"][1:]]
        self.make(self.politics)                                  # the master is gone: the cast check's clean still
        self.assertEqual([Path(p).read_bytes() for p in stills()], [clean] * 3)
        self.assertEqual(self.grabs, [])
        master.parent.mkdir(parents=True, exist_ok=True)
        master.write_bytes(b"the broadcaster's own feed")
        self.make(self.politics, again=True)                      # the master is here: its frame at each clip's moment
        self.assertEqual(self.grabs, [(str(master), 116.0)] * 3)
        self.assertEqual([Path(p).read_bytes() for p in stills()], [b"\xff\xd8\xff\xe0grab\xff\xd9"] * 3)
        master.unlink()
        shutil.rmtree(jd / "castcheck")
        self.grabs.clear()
        self.make(self.politics, again=True)                      # neither: no picture, never the cover
        self.assertEqual((stills(), self.grabs), (["", "", ""], []))


# ---------------------------------------------------------------------------------------------- outputs and the engine
class OutputsTest(Base):
    def test_1_nothing_is_written_outside_state(self):
        self.set(self.politics, {"cards.make": "on"}, {"cards.targets": ["facebook_story", "instagram_story"]})
        self.politics_clip()

        def listing(root):
            return sorted(str(p) for p in Path(root).rglob("*") if p.is_file())
        before = {r: listing(self.tmp / r) for r in ("drafts", "delivered", "accounts", "clipwork")}
        self.make(self.politics, kinds=["quote"])
        for r, files in before.items():
            self.assertEqual(listing(self.tmp / r), files, r)
        card = CD.get_card(self.politics, DAY, "quote-0700")
        self.files_under_state(card)
        for p in CD.card_dir(self.politics, DAY, "quote-0700").iterdir():
            self.assertNotIn(".part", p.name)

    def test_2_a_card_folder_outside_state_is_refused(self):
        with mock.patch.object(CD, "card_dir", lambda a, d, cid: self.tmp / "drafts" / cid):
            with self.assertRaises(SystemExit):
                CD.render(self.cfg, self.gta, DAY, {"id": "x", "kind": "countdown", "targets": []},
                          [{"layout": "countdown", "days": 3}], (1080, 1920))

    def test_3_an_engine_that_fails_says_why(self):
        bad = self.tmp / "bad_engine.py"
        bad.write_text('import json\nprint(json.dumps({"ok": False, "error": "no fonts"}))\nraise SystemExit(1)\n')
        with mock.patch.object(CD, "ENGINE", bad):
            with self.assertRaises(SystemExit) as ex:
                CD.render(self.cfg, self.gta, DAY, {"id": "x", "kind": "countdown", "targets": []},
                          [{"layout": "countdown", "days": 3}], (1080, 1920))
        self.assertIn("no fonts", str(ex.exception))

    def test_4_the_engine_is_run_as_the_skills_run_theirs(self):
        seen = {}

        def run(cmd, **kw):
            seen.update(cmd=cmd, **kw)
            for o in json.loads(Path(cmd[-1]).read_text())["outputs"]:
                Path(o).write_bytes(b"\xff\xd8x\xff\xd9")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"ok": True, "engine": "card_engine 1"}) + "\n", "")
        with mock.patch.object(CD.subprocess, "run", run), mock.patch.dict(os.environ, {"CLIP_ENGINE_PYTHON": "",
                                                                                     "CLIP_FACTORY_OFFLINE": "1"}):
            CD.render(self.cfg, self.gta, DAY, {"id": "x", "kind": "countdown", "targets": []},
                      [{"layout": "countdown", "days": 3}], (1080, 1920))
        self.assertEqual(seen["cmd"][:3], ["/usr/bin/python3", "-B", str(self.eng)])
        self.assertEqual((seen["timeout"], seen["env"]["CLIP_FACTORY_OFFLINE"]), (60, "1"))
        for word in ("run.py", "clipkit", "analyze", "build_all"):
            self.assertNotIn(word, Path(CD.ENGINE).name)
            self.assertNotIn(word, "card_engine.py")


@unittest.skipUnless(_engine_has_pil(), "the engine Python (/usr/bin/python3) has no Pillow, fontTools or fonts here")
class RealEngineTest(unittest.TestCase):
    """The real render/card_engine.py under /usr/bin/python3, one card of each kind, into a throwaway folder."""

    def test_1_each_kind_draws(self):
        engine = Path(__file__).resolve().parent.parent / "render" / "card_engine.py"
        tmp = Path(tempfile.mkdtemp(prefix="clipfactory-cards-real-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        still = tmp / "still.jpg"
        subprocess.run([ENGINE_PY, "-c", "from PIL import Image; Image.new('RGB', (854, 480), (60, 70, 80))"
                        f".save({str(still)!r}, 'JPEG')"], check=True, timeout=30)
        vce = Path(os.path.expanduser("~/.claude/skills/viral-clip-engine"))
        gta = Path(os.path.expanduser("~/.claude/skills/gaming"))
        specs = []
        if (vce / "scripts" / "politicslook.py").is_file():
            specs += [("politics", "quote", [1080, 1920], [{"layout": "quote", "still": str(still), "quote": QUOTE,
                                                          "name": "Pat Ross", "title": "TREASURY SECRETARY",
                                                          "source": "ABC News · NOVEMBER 1 2026"}], vce),
                      ("politics", "docket", [1080, 1350], [
                          {"layout": "docket_cover", "label": "THE DOCKET", "figure": "3", "sub": "clips",
                           "date": "November 1, 2026", "source": ""},
                          {"layout": "docket_item", "still": str(still), "title": "A title 25 words", "time": "7:00 AM",
                           "host": "ABC News", "tab": "THE DOCKET", "source": "ABC News"}], vce),
                      ("politics", "week_best", [1080, 1350], [
                          {"layout": "best_cover", "label": "THE WEEK'S BEST", "figure": "3", "sub": "most watched",
                           "date": "Oct 26 to Nov 1", "source": ""},
                          {"layout": "best_item", "still": str(still), "title": "A title", "time": "Sunday 7:00 AM",
                           "host": "ABC News", "tab": "No. 1", "figure": "19.2K views", "source": "ABC News"}], vce)]
        if (gta / "assets" / "brand" / "gta" / "kit.json").is_file():
            specs += [("gta", "countdown", [1080, 1920], [{"layout": "countdown", "days": 17,
                                                           "date": "November 19, 2026", "still": str(still)}], gta),
                      ("gta", "countdown_ink", [1080, 1920], [{"layout": "countdown", "days": 0,
                                                               "date": "November 19, 2026"}], gta),
                      ("gta", "clip_of_day", [1080, 1920], [{"layout": "clip_of_day", "still": str(still),
                                                             "hook": "A house like this \U0001F525", "credit": "From X",
                                                             "label": "Clip of the day"}], gta),
                      ("gta", "week_best", [1080, 1350], [{"layout": "best_cover", "label": "THIS WEEK'S",
                                                           "figure": "TOP 3", "date": "Oct 26 to Nov 1"},
                                                          {"layout": "best_item", "rank": "#1", "title": "A title",
                                                           "credit": "From X", "figure": "1.2K views"}], gta)]
        if not specs:
            self.skipTest("neither skill is on this Mac")
        for look, kind, size, slides, skill in specs:
            outs = [str(tmp / kind / f"{i + 1}.jpg") for i in range(len(slides))]
            sp = tmp / f"{look}-{kind}.json"
            sp.write_text(json.dumps({"v": 1, "look": look, "kind": kind, "skill_dir": str(skill), "kit": "gta",
                                      "size": size, "slides": slides, "outputs": outs}))
            r = subprocess.run([ENGINE_PY, "-B", str(engine), str(sp)], capture_output=True, text=True, timeout=60)
            got = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertTrue(got.get("ok"), (kind, got, r.stderr[-400:]))
            for f in got["files"]:
                self.assertEqual((f["w"], f["h"]), tuple(size))
                self.assertEqual(Path(f["path"]).read_bytes()[:3], b"\xff\xd8\xff")
                self.assertFalse(Path(f["path"]).with_name(Path(f["path"]).stem + ".part.jpg").exists())
            dest = os.environ.get("CARDS_PREVIEW_DIR")
            if dest:
                for f in got["files"]:
                    Path(dest).mkdir(parents=True, exist_ok=True)
                    shutil.copy(f["path"], Path(dest) / f"{look}-{kind}-{Path(f['path']).name}")
        r = subprocess.run([ENGINE_PY, "-B", str(engine), "--selftest"] + (["--skill", str(vce)] if vce.is_dir()
                                                                           else []),
                           capture_output=True, text=True, timeout=60)
        self.assertTrue(json.loads(r.stdout.strip().splitlines()[-1])["ok"], r.stdout)


# the ink of a drawn card: every pixel that differs from its ground (the corner) by more than JPEG noise
_MEASURE = """
import json, sys
from PIL import Image, ImageChops
out = []
for p in sys.argv[1:]:
    im = Image.open(p).convert("RGB")
    ground = Image.new("RGB", im.size, im.getpixel((3, 3)))
    ink = ImageChops.difference(im, ground).convert("L").point(lambda v: 255 if v > 48 else 0)
    out.append([ink.getbbox(), im.getpixel((3, 3)), im.getpixel((540, 1900 if im.height > 1400 else 1340))])
print(json.dumps(out))
"""


@unittest.skipUnless(_engine_has_pil(), "the engine Python (/usr/bin/python3) has no Pillow, fontTools or fonts here")
class CentringTest(unittest.TestCase):
    """2026-10-20: each card is centred on the CANVAS (x 540), not on a Reel's safe zone (x 65..916, centre 490, where
    the GTA lockup, the big number and the clip of the day's frame used to sit); a story keeps its top 250 and its
    bottom 340 clear and is balanced, as one block, in the band between (centre y 915); a feed slide has even margins.
    Measured on the pixels the real engine draws over flat grounds."""

    def test_1_every_kind_sits_square_on_the_frame(self):
        engine = Path(__file__).resolve().parent.parent / "render" / "card_engine.py"
        tmp = Path(tempfile.mkdtemp(prefix="clipfactory-cards-centre-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        still = tmp / "still.jpg"
        subprocess.run([ENGINE_PY, "-c", "from PIL import Image; Image.new('RGB', (1920, 1080), (150, 150, 150))"
                        f".save({str(still)!r}, 'JPEG', quality=95)"], check=True, timeout=30)
        vce = Path(os.path.expanduser("~/.claude/skills/viral-clip-engine"))
        gta = Path(os.path.expanduser("~/.claude/skills/gaming"))
        st = str(still)
        title = "A house like this being the versetti estate"
        cases = []
        if (vce / "scripts" / "politicslook.py").is_file():
            cases += [("politics quote", "politics", vce, [1080, 1920],
                       {"layout": "quote", "still": st, "quote": QUOTE, "name": "Pat Ross",
                        "title": "TREASURY SECRETARY", "source": "ABC News · NOVEMBER 1 2026"}),
                      ("politics docket cover", "politics", vce, [1080, 1350],
                       {"layout": "docket_cover", "label": "THE DOCKET", "figure": "7", "sub": "clips on the record",
                        "date": "November 1, 2026", "source": ""}),
                      ("politics docket item", "politics", vce, [1080, 1350],
                       {"layout": "docket_item", "still": st, "title": "Ross would not say if the cut was coming",
                        "time": "7:00 AM", "host": "ABC News", "tab": "THE DOCKET", "source": "ABC News"}),
                      ("politics docket item, no picture", "politics", vce, [1080, 1350],
                       {"layout": "docket_item", "still": "", "title": "Ross would not say if the cut was coming",
                        "time": "7:00 AM", "host": "ABC News", "tab": "THE DOCKET", "source": "ABC News"}),
                      ("politics week cover", "politics", vce, [1080, 1350],
                       {"layout": "best_cover", "label": "THE WEEK'S BEST", "figure": "5", "sub": "most watched",
                        "date": "October 26 to November 1, 2026", "source": ""}),
                      ("politics week item", "politics", vce, [1080, 1350],
                       {"layout": "best_item", "still": st, "title": "Ross would not say if the cut was coming",
                        "time": "Sunday 7:00 AM", "host": "ABC News", "tab": "No. 1", "figure": "19.2K views",
                        "source": "ABC News"})]
        if (gta / "assets" / "brand" / "gta" / "kit.json").is_file():
            cases += [("gta countdown", "gta", gta, [1080, 1920],
                       {"layout": "countdown", "days": 30, "date": "November 19, 2026", "still": st}),
                      ("gta countdown, no frame given", "gta", gta, [1080, 1920],
                       {"layout": "countdown", "days": 17, "date": "November 19, 2026"}),
                      ("gta launch day", "gta", gta, [1080, 1920],
                       {"layout": "countdown", "days": 0, "date": "November 19, 2026", "still": st}),
                      ("gta clip of the day", "gta", gta, [1080, 1920],
                       {"layout": "clip_of_day", "still": st, "hook": title, "credit": "From MrBossFTW",
                        "label": "Clip of the day"}),
                      ("gta clip of the day, no clean frame", "gta", gta, [1080, 1920],
                       {"layout": "clip_of_day", "still": "", "hook": title, "credit": "From MrBossFTW",
                        "label": "Clip of the day"}),
                      ("gta week cover", "gta", gta, [1080, 1350],
                       {"layout": "best_cover", "label": "THIS WEEK'S", "figure": "TOP 5",
                        "date": "October 26 to November 1, 2026"}),
                      ("gta week item", "gta", gta, [1080, 1350],
                       {"layout": "best_item", "rank": "#1", "title": title, "credit": "From MrBossFTW",
                        "figure": "19.2K views"})]
        if not cases:
            self.skipTest("neither skill is on this Mac")
        outs = []
        for i, (name, look, skill, size, slide) in enumerate(cases):
            out = str(tmp / f"{i}.jpg")
            sp = tmp / f"{i}.json"
            sp.write_text(json.dumps({"v": 1, "look": look, "kind": "x", "skill_dir": str(skill), "kit": "gta",
                                      "size": size, "slides": [slide], "outputs": [out]}))
            r = subprocess.run([ENGINE_PY, "-B", str(engine), str(sp)], capture_output=True, text=True, timeout=60)
            self.assertTrue(json.loads(r.stdout.strip().splitlines()[-1]).get("ok"), (name, r.stdout, r.stderr[-300:]))
            outs.append(out)
        r = subprocess.run([ENGINE_PY, "-c", _MEASURE] + outs, capture_output=True, text=True, timeout=60)
        measured = json.loads(r.stdout.strip().splitlines()[-1])
        for (name, _look, _skill, size, slide), (box, corner, foot) in zip(cases, measured):
            with self.subTest(name, box=box):
                if slide["layout"] == "countdown":           # handed a frame, it still draws on the kit's flat ink
                    for px in (corner, foot):
                        self.assertLessEqual(max(abs(a - b) for a, b in zip(px, (10, 7, 19))), 6, "a frame showed")
                x0, y0, x1, y1 = box
                self.assertLessEqual(abs((x0 + x1) / 2 - 540), 4, "not centred on the canvas")
                self.assertGreaterEqual(x0 if x1 - x0 < 1080 else 72, 72 - 24, "outside the left margin")
                self.assertLessEqual(x1 if x1 - x0 < 1080 else 1008, 1008 + 24, "outside the right margin")
                if size[1] == 1920:
                    self.assertGreaterEqual(y0, 250 - 12, "under the story's top bar")
                    self.assertLessEqual(y1, 1920 - 340, "under the story's reply bar")
                    self.assertLessEqual(abs((y0 + y1) / 2 - 915), 16, "not balanced in the story's band")
                else:
                    self.assertLessEqual(abs(y0 - (size[1] - y1)), 24, "uneven margins top and bottom")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "no ffmpeg here")
class RealMp4Test(unittest.TestCase):
    def test_1_the_instagram_copy_is_six_seconds_with_sound(self):
        tmp = Path(tempfile.mkdtemp(prefix="clipfactory-cards-mp4-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        jpg = tmp / "1.jpg"
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=gray:s=1080x1920", "-frames:v", "1", str(jpg)], check=True, timeout=60)
        CD._to_mp4(jpg, tmp / "1.mp4")
        self.assertFalse((tmp / "1.part.mp4").exists())
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                           str(tmp / "1.mp4")], capture_output=True, text=True, timeout=60).stdout)
        kinds = sorted(s["codec_type"] for s in probe["streams"])
        self.assertEqual(kinds, ["audio", "video"])
        self.assertAlmostEqual(float(probe["format"]["duration"]), 6.0, delta=0.2)
        v = next(s for s in probe["streams"] if s["codec_type"] == "video")
        self.assertEqual((v["width"], v["height"], v["pix_fmt"]), (1080, 1920, "yuv420p"))


# ---------------------------------------------------------------------------------------------- story reposts
class StoryRepostTest(Base):
    def setUp(self):
        super().setUp()
        self.set(self.gta, {"cards.story_repost": "practice"})
        self.posted(self.gta, DAY, "10:30", published=self.cfg.slot_time(DAY, "10:30"))
        self.slot = B.read(self.gta, DAY)["slots"]["10:30"]
        self.rec = self.slot["outlets"]["instagram_reels"]

    def sign_rule(self, **fields):
        return CD.sign_rule(self.cfg, self.gta, fields, None, "Andrew", self.at("07:00"))

    def follow(self, hhmm="10:31"):
        return CD.on_posted(self.cfg, self.gta, DAY, B.read(self.gta, DAY)["slots"]["10:30"], "instagram_reels",
                            self.rec, self.at(hhmm))

    def test_1_an_unsigned_rule_queues_nothing(self):
        self.assertIsNone(self.follow())
        self.assertEqual(OW.due(self.cfg, self.gta, self.at("12:00"), runner="studio"), [])
        self.sign_rule()
        CD.pause_rule(self.cfg, self.gta, "Andrew")
        self.assertIsNone(self.follow())

    def test_2_the_signed_bytes_go_30_to_60_minutes_after_the_reel(self):
        self.sign_rule()
        words = self.follow()
        self.assertIn("queued", words)
        items = list(OW._items(self.gta.slug).values())
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it["kind"], it["runner"], it["key"], it["outlet"]),
                         ("story", "studio", f"story:instagram:{DAY} 10:30", "instagram_story"))
        self.assertEqual(it["signed"]["what"], "cards-rule:story_repost@v1")
        self.assertEqual(it["target"]["sha"], self.slot["clip"]["sha256"])
        live = datetime.fromisoformat(self.rec["published_at"])
        nb = datetime.fromisoformat(it["not_before"])
        self.assertTrue(timedelta(minutes=30) <= nb - live <= timedelta(minutes=60), nb - live)
        self.assertIsNone(self.follow())                                # once per Reel
        self.assertEqual(CD.still_signed(self.cfg, self.gta, it), "")
        CD.pause_rule(self.cfg, self.gta, "Andrew")
        self.assertIn("not signed", CD.still_signed(self.cfg, self.gta, it))

    def test_3_over_sixty_seconds_is_refused(self):
        self.sign_rule()
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["10:30"]["clip"]["seconds"] = 72.5
        words = self.follow()
        self.assertIn("over a story's 60", words)
        self.assertEqual(list(OW._items(self.gta.slug).values()), [])
        st = CD.read_board(self.gta, DAY)["stories"]["10:30|instagram"]
        self.assertEqual(st["state"], "skipped")

    def test_4_a_politics_clip_over_sixty_goes_only_with_its_cut(self):
        self.set(self.politics, {"cards.story_repost": "practice"})
        CD.sign_rule(self.cfg, self.politics, {}, None, "Andrew", self.at("07:00"))
        self.posted(self.politics, DAY, "10:30", published=self.cfg.slot_time(DAY, "10:30"))
        with B.edit(self.cfg, self.politics, DAY) as bd:
            bd["slots"]["10:30"]["clip"]["seconds"] = 80.0
        s = B.read(self.politics, DAY)["slots"]["10:30"]
        words = CD.on_posted(self.cfg, self.politics, DAY, s, "facebook_reels", s["outlets"]["facebook_reels"],
                             self.at("10:31"))
        self.assertIn("signed Instagram cut", words)

    def test_5_the_day_has_its_most(self):
        self.sign_rule(per_day=1)
        self.posted(self.gta, DAY, "12:15", published=self.cfg.slot_time(DAY, "12:15"))
        self.assertIn("queued", self.follow())
        s = B.read(self.gta, DAY)["slots"]["12:15"]
        words = CD.on_posted(self.cfg, self.gta, DAY, s, "facebook_reels", s["outlets"]["facebook_reels"],
                             self.at("12:16"))
        self.assertIn("used", words)

    def test_6_the_rule_checks_its_numbers(self):
        for bad in ({"platforms": ["youtube"]}, {"per_day": 9}, {"delay_min": [10, 20]}, {"which": "best"}):
            with self.assertRaises(SystemExit, msg=str(bad)):
                self.sign_rule(**bad)
        words = self.sign_rule(platforms=["facebook"], per_day=2, delay_min=[45, 50])
        self.assertIn("version 2", words)
        r = CD.read_rule(self.gta)
        self.assertTrue(CD.rule_signed(r))
        self.assertEqual((r["platforms"], r["per_day"], r["delay_min"]), (["facebook"], 2, [45, 50]))
        with self.assertRaises(SystemExit):
            CD.sign_rule(self.cfg, self.gta, {}, 1, "Andrew")             # the page showed version 1

    def test_7_switch_off_reads_and_writes_nothing(self):
        self.set(self.gta, {"cards.story_repost": "off"})
        self.sign_rule()
        shutil.rmtree(store.path(self.gta.slug, "cards"))
        self.assertIsNone(self.follow())
        self.assertFalse(store.path(self.gta.slug, "cards").exists())


# ---------------------------------------------------------------------------------------------- after its clip
class AfterTest(Base):
    def setUp(self):
        super().setUp()
        self.set(self.politics, {"cards.make": "on", "cards.post": "practice"},
                 {"cards.targets": ["facebook_story"]})
        self.politics_clip()
        self.make(self.politics, kinds=["quote"])
        c = CD.get_card(self.politics, DAY, "quote-0700")
        CD.approve(self.cfg, self.politics, DAY, "quote-0700", CD.card_hash(c), "Andrew", self.at("06:50"))

    def test_1_skipped_when_its_clip_did_not_post(self):
        self.assertEqual(CD._advance(self.cfg, self.politics, DAY, self.at("09:30")), [])
        lines = CD._advance(self.cfg, self.politics, DAY, self.at("10:01"))
        self.assertIn("its clip did not post", lines[0])
        self.assertEqual(CD.get_card(self.politics, DAY, "quote-0700")["status"], "skipped")

    def test_2_waits_for_its_clip_then_goes_60_to_90_minutes_after(self):
        self.sign_clip(self.politics, DAY, "07:00")
        live = self.cfg.slot_time(DAY, "07:02")
        with B.edit(self.cfg, self.politics, DAY) as bd:
            bd["slots"]["07:00"]["outlets"] = {"instagram_reels": {"post_id": "9", "published_at": live.isoformat(),
                                                                   "provider": "direct"}}
        CD._advance(self.cfg, self.politics, DAY, self.at("07:10"))
        c = CD.get_card(self.politics, DAY, "quote-0700")
        pa = datetime.fromisoformat(c["post_at"])
        self.assertTrue(timedelta(minutes=60) <= pa - live <= timedelta(minutes=90))
        self.assertEqual(OW.due(self.cfg, self.politics, self.at("09:00"), runner="studio"), [])
        CD._advance(self.cfg, self.politics, DAY, pa.astimezone(self.cfg.tz()) + timedelta(minutes=1))
        c = CD.get_card(self.politics, DAY, "quote-0700")
        self.assertEqual(c["status"], "scheduled")
        items = list(OW._items(self.politics.slug).values())
        self.assertEqual([i["key"] for i in items], [f"card:facebook:{DAY}/quote-0700:facebook_story"])
        self.assertEqual(items[0]["signed"], {"what": f"card:{DAY}/quote-0700", "by": "Andrew",
                                              "at": c["signoff"]["at"], "hash": c["signoff"]["hash"]})

    def test_3_a_clip_made_again_skips_its_card(self):
        with B.edit(self.cfg, self.politics, DAY) as bd:
            bd["slots"]["07:00"]["clip"]["sha256"] = "0" * 64
        lines = CD._advance(self.cfg, self.politics, DAY, self.at("07:30"))
        self.assertIn("its clip changed", lines[0])


# ---------------------------------------------------------------------------------------------- posting
class PostTest(Base):
    """Each route a card or a story takes, through outward.send and fake Graph answers."""

    def setUp(self):
        super().setUp()
        self.grant(self.gta)
        self.g = FakeGraph()
        patch_http(self, graph=self.g)

    def card(self, kind, targets, hhmm="09:00"):
        kinds = {"countdown": ["countdown"], "week_best": ["week_best"]}[kind]
        self.set(self.gta, {"cards.make": "on", "cards.post": "on"}, {"cards.kinds": kinds, "cards.targets": targets,
                                                                      "cards.times": {"week_best": f"Mon {hhmm}"}})
        if kind == "week_best":
            for i, k in enumerate(("07:00", "08:45", "10:30")):
                self.posted(self.gta, DAY - timedelta(days=i + 1), k, views={k: 100 * (i + 1)})
        self.make(self.gta)
        cid = f"{kind}-{hhmm.replace(':', '')}"
        c = CD.get_card(self.gta, DAY, cid)
        CD.approve(self.cfg, self.gta, DAY, cid, CD.card_hash(c), "Andrew", self.at("07:00"))
        return cid

    def run_at(self, hhmm, times=1):
        out = []
        for _ in range(times):
            out += CD.tick(self.cfg, self.at(hhmm))
        return out

    def route_photo_story(self):
        self.g.on("POST", rf"/{PAGE}/photos$", lambda c: {"id": f"ph{len(self.g.calls)}"})
        self.g.on("POST", rf"/{PAGE}/photo_stories$", {"success": True, "post_id": "story1"})

    def test_1_facebook_photo_story(self):
        cid = self.card("countdown", ["facebook_story"])
        self.route_photo_story()
        self.assertEqual(self.run_at("08:59"), [])
        lines = self.run_at("09:00")
        self.assertTrue(any("sent" in ln for ln in lines), lines)
        self.assertEqual(self.g.paths(), [f"POST /{PAGE}/photos", f"POST /{PAGE}/photo_stories"])
        up = self.g.calls[0]
        self.assertIn(b'name="published"\r\n\r\nfalse', up["data"])
        self.assertIn(b'name="source"; filename="1.jpg"', up["data"])
        self.assertEqual(self.g.calls[1]["form"]["photo_id"], "ph1")
        c = CD.get_card(self.gta, DAY, cid)
        self.assertEqual(c["status"], "posted")
        out = c["outlets"]["facebook_story"]
        self.assertEqual((out["state"], out["post_id"], out["provider"]), ("done", "story1", "direct"))
        self.assertEqual(out["attempt"]["photo_id"], "ph1")             # written before the story went
        self.run_at("09:10")
        self.assertEqual(len(self.g.calls), 2)                          # never twice

    def test_2_practice_sends_nothing(self):
        cid = self.card("countdown", ["facebook_story"])
        self.set(self.gta, {"cards.post": "practice"})
        self.run_at("09:00")
        self.assertEqual(self.g.calls, [])
        c = CD.get_card(self.gta, DAY, cid)
        self.assertEqual((c["status"], c["outlets"]["facebook_story"]["state"]), ("posted", "practice"))

    def test_3_facebook_feed_carousel(self):
        cid = self.card("week_best", ["facebook_feed"], hhmm="12:00")
        self.g.on("POST", rf"/{PAGE}/photos$", lambda c: {"id": f"ph{len(self.g.calls)}"})
        self.g.on("POST", rf"/{PAGE}/feed$", {"id": f"{PAGE}_555"})
        self.run_at("12:00")
        self.assertEqual(self.g.paths(), [f"POST /{PAGE}/photos"] * 4 + [f"POST /{PAGE}/feed"])
        form = self.g.calls[-1]["form"]
        self.assertEqual(json.loads(form["attached_media[0]"]), {"media_fbid": "ph1"})
        self.assertEqual(json.loads(form["attached_media[3]"]), {"media_fbid": "ph4"})
        self.assertEqual(CD.get_card(self.gta, DAY, cid)["outlets"]["facebook_feed"]["post_id"], f"{PAGE}_555")

    def ig_routes(self, usage=3):
        n = {"c": 0}

        def media(call):
            n["c"] += 1
            return {"id": f"c{n['c']}", "uri": f"https://rupload.facebook.com/ig-api-upload/v25.0/c{n['c']}"}
        self.g.on("GET", rf"/{IG}/content_publishing_limit$", {"data": [{"config": {"quota_total": 50},
                                                                          "quota_usage": usage}]})
        self.g.on("POST", rf"/{IG}/media$", media)
        self.g.on("POST", r"rupload\.facebook\.com/ig-api-upload/", {"success": True})
        self.g.on("GET", r"^/c\d+$", {"status_code": "FINISHED"})
        self.g.on("POST", rf"/{IG}/media_publish$", {"id": "m77"})
        self.g.on("GET", r"^/m77$", {"permalink": "https://www.instagram.com/stories/x/m77/"})

    def test_4_instagram_story_from_the_mp4(self):
        cid = self.card("countdown", ["instagram_story"])
        self.ig_routes()
        self.run_at("09:00")
        paths = self.g.paths()
        self.assertEqual(paths[0], f"GET /{IG}/content_publishing_limit")
        self.assertIn(f"POST /{IG}/media", paths)
        made = next(c for c in self.g.calls if c["path"] == f"/{IG}/media")
        self.assertEqual((made["form"]["media_type"], made["form"]["upload_type"]), ("STORIES", "resumable"))
        up = next(c for c in self.g.calls if "rupload" in c["url"])
        mp4 = CD.get_card(self.gta, DAY, cid)["files"]["videos"][0]["path"]
        self.assertEqual(up["data"], Path(mp4).read_bytes())
        self.assertEqual(up["headers"]["offset"], "0")
        self.assertTrue(up["headers"]["Authorization"].startswith("OAuth "))
        self.assertNotIn("access_token", up["url"] + json.dumps(up.get("params") or {}))   # as a Reel's bytes go
        self.assertEqual(self.g.calls[-2]["form"]["creation_id"], "c1")
        c = CD.get_card(self.gta, DAY, cid)
        self.assertEqual(c["outlets"]["instagram_story"]["post_id"], "m77")
        self.assertEqual(c["outlets"]["instagram_story"]["attempt"]["container_id"], "c1")

    def test_5_instagram_carousel_from_video_children(self):
        self.card("week_best", ["instagram_carousel"], hhmm="12:00")
        self.ig_routes()
        self.run_at("12:00")
        made = [c["form"] for c in self.g.calls if c["path"] == f"/{IG}/media"]
        self.assertEqual([m["media_type"] for m in made], ["VIDEO"] * 4 + ["CAROUSEL"])
        self.assertTrue(all(m["is_carousel_item"] == "true" for m in made[:4]))
        self.assertEqual(made[-1]["children"], "c1,c2,c3,c4")
        self.assertEqual(self.g.calls[-2]["form"]["creation_id"], "c5")

    def test_6_instagram_keeps_room_for_the_reels(self):
        self.assertEqual(API.room_words({"quota_total": 50, "quota_usage": 20}, 4), "")
        self.assertIn("Reels keep their room", API.room_words({"quota_total": 50, "quota_usage": 20}, 5))
        self.assertIn("Reels keep their room", API.room_words({"quota_total": 30, "quota_usage": 14}, 1))
        cid = self.card("countdown", ["instagram_story"])
        self.ig_routes(usage=24)
        slot_ready(self, self.gta, DAY, "19:15")
        self.sign_clip(self.gta, DAY, "19:15")
        self.run_at("09:00")
        self.assertEqual(self.g.paths(), [f"GET /{IG}/content_publishing_limit"])
        self.assertEqual(len(OW.due(self.cfg, self.gta, self.at("09:00"), runner="studio")), 1)   # it waits
        self.assertEqual(CD.get_card(self.gta, DAY, cid)["status"], "scheduled")
        self.assertEqual(store.read(store.path(self.gta.slug, "cards", "limit.json"))["quota_usage"], 24)

    def test_7_never_within_30_minutes_of_a_reel(self):
        self.card("countdown", ["facebook_story"])
        self.route_photo_story()
        slot_ready(self, self.gta, DAY, "08:45")
        self.sign_clip(self.gta, DAY, "08:45")
        self.run_at("09:00")
        self.assertEqual(self.g.calls, [])
        self.run_at("09:16")
        self.assertEqual(len(self.g.calls), 2)

    def test_8_a_lost_answer_is_looked_up_never_sent_again(self):
        cid = self.card("countdown", ["facebook_story"])
        self.g.on("POST", rf"/{PAGE}/photos$", {"id": "ph1"})
        self.g.on("POST", rf"/{PAGE}/photo_stories$", TimeoutError("no answer"))
        self.run_at("09:00")
        self.assertEqual(CD.get_card(self.gta, DAY, cid)["outlets"]["facebook_story"]["attempt"]["photo_id"], "ph1")
        when = self.cfg.slot_time(DAY, "09:00").astimezone(timezone.utc)
        self.g.on("GET", rf"/{PAGE}/stories$", {"data": [{"post_id": "found1", "creation_time": when.isoformat()}]})
        self.run_at("09:11")
        c = CD.get_card(self.gta, DAY, cid)
        self.assertEqual((c["status"], c["outlets"]["facebook_story"]["post_id"]), ("posted", "found1"))
        self.assertEqual([p for p in self.g.paths() if p.endswith("photo_stories")], [f"POST /{PAGE}/photo_stories"])

    def test_9_story_reposts_through_both_platforms(self):
        self.set(self.gta, {"cards.story_repost": "on"})
        CD.sign_rule(self.cfg, self.gta, {}, None, "Andrew", self.at("07:00"))
        self.posted(self.gta, DAY, "10:30", published=self.cfg.slot_time(DAY, "10:30"))
        s = B.read(self.gta, DAY)["slots"]["10:30"]
        for outlet in ("facebook_reels", "instagram_reels"):
            CD.on_posted(self.cfg, self.gta, DAY, s, outlet, s["outlets"][outlet], self.at("10:31"))

        def vs(call):
            if call["form"]["upload_phase"] == "start":
                return {"video_id": "v9", "upload_url": "https://rupload.facebook.com/video-upload/v25.0/v9"}
            return {"success": True, "post_id": "vstory9"}
        self.g.on("POST", rf"/{PAGE}/video_stories$", vs)
        self.g.on("POST", r"rupload\.facebook\.com/video-upload/", {"success": True})
        self.ig_routes()
        for t in ("11:05", "11:40", "12:30", "13:20"):
            self.run_at(t)
        st = CD.read_board(self.gta, DAY)["stories"]
        self.assertEqual((st["10:30|facebook"]["state"], st["10:30|facebook"]["post_id"]), ("done", "vstory9"))
        self.assertEqual((st["10:30|instagram"]["state"], st["10:30|instagram"]["post_id"]), ("done", "m77"))
        fin = [c["form"] for c in self.g.calls if c["path"] == f"/{PAGE}/video_stories"]
        self.assertEqual([f["upload_phase"] for f in fin], ["start", "finish"])
        clip = Path(s["clip"]["path"]).read_bytes()
        self.assertTrue(all(c["data"] == clip for c in self.g.calls if "rupload" in c["url"]))
        made = next(c["form"] for c in self.g.calls if c["path"] == f"/{IG}/media")
        self.assertEqual(made["media_type"], "STORIES")
        self.assertEqual(self.g.unmatched, [])

    def test_10_no_delete_ever(self):
        text = (Path(CD.__file__).read_text() + Path(API.__file__).read_text())
        for word in ('"DELETE"', "videos.delete", "_http("):
            self.assertNotIn(word, text)


# ---------------------------------------------------------------------------------------------- the clock
class NightTest(Base):
    def setUp(self):
        super().setUp()
        self.set(self.gta, {"cards.make": "on", "cards.post": "on"})

    def test_1_no_work_between_21_00_and_06_25(self):
        for hhmm in ("21:00", "23:59", "02:00", "06:15", "06:25"):
            d = DAY if hhmm >= "06" else DAY + timedelta(days=1)
            self.assertEqual(CD.tick(self.cfg, self.at(hhmm, d)), [], hhmm)
        self.assertEqual(FakeEngine.calls(self.eng.parent), [])
        self.assertFalse(CD.board_path(self.gta, DAY).exists())
        with self.assertRaises(SystemExit) as ex:
            CD.make(self.cfg, self.gta, DAY, self.at("21:30"))
        self.assertIn("06:26", str(ex.exception))
        CD.tick(self.cfg, self.at("06:26"))
        self.assertTrue(CD.board_path(self.gta, DAY).exists())
        self.assertTrue(FakeEngine.calls(self.eng.parent))

    def test_2_the_studio_job_runs_it(self):
        self.assertEqual(REG.JOBS["studio"][0], "cards:tick")
        SOC.run_job(self.cfg, "studio", quiet=True, now=self.at("06:30"))
        self.assertTrue(CD.read_board(self.gta, DAY).get("made"))


# ---------------------------------------------------------------------------------------------- off, and the surfaces
class OffTest(Base):
    def test_1_off_means_nothing(self):
        now = self.at("12:00")
        self.posted(self.gta, DAY, "10:30", published=self.cfg.slot_time(DAY, "10:30"))
        s = B.read(self.gta, DAY)["slots"]["10:30"]
        self.assertEqual(CD.tick(self.cfg, now), [])
        self.assertIsNone(CD.on_posted(self.cfg, self.gta, DAY, s, "instagram_reels", s["outlets"]["instagram_reels"],
                                       now))
        accts = list(self.cfg.accounts.values())
        self.assertEqual((CD.health(self.cfg, now, accts), CD.summary(self.cfg, now, accts),
                          CD.morning_line(self.cfg, now), CD.cal_items(self.cfg, now, accts)),
                         (("", ""), [], "", {}))
        self.assertTrue(all(r[0] is None for r in CD.doctor(self.cfg, now)))
        for a in accts:
            self.assertFalse(store.path(a.slug, "cards").exists())
        self.assertEqual(FakeEngine.calls(self.eng.parent), [])

    def test_2_the_surfaces_once_on(self):
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["countdown"]})
        CD.tick(self.cfg, self.at("06:30"))
        now = self.at("07:00")
        accts = list(self.cfg.accounts.values())
        self.assertEqual(CD.summary(self.cfg, now, accts), [{"key": "cards", "words": "Cards: 1 to sign",
                                                              "href": "/cards", "level": "calm", "order": 40}])
        self.assertEqual(CD.morning_line(self.cfg, now), "1 card to sign")
        self.assertEqual(CD.cal_items(self.cfg, now, accts), {DAY.isoformat(): {"gaming": [{"k": "card",
                                                                                               "s": "review"}]}})
        self.assertIn("Cards: 1 to sign", [c["words"] for c in SOC.brief(self.cfg, now)])
        self.assertIn("1 card to sign", SOC.morning_lines(self.cfg, now)[-1])
        with B.edit(self.cfg, self.gta, DAY):
            pass
        bd = CD.read_board(self.gta, DAY)
        c = bd["cards"]["countdown-0900"]
        with CD.edit_board(self.gta, DAY) as b2:
            b2["cards"]["countdown-0900"] = dict(c, status="failed", signoff={"hash": "x"})
        self.assertEqual(CD.health(self.cfg, now, accts)[0], "bad")

    def test_3_a_board_left_after_cards_go_off(self):
        # Review fix 2026-10-20: cal_items read every board whatever the switches said, so a ready and an approved
        # card left from when cards were on stayed on the /live calendar as "review" and "scheduled" after every
        # cards switch went off, while health, summary and tick all said nothing.
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["countdown"]})
        CD.tick(self.cfg, self.at("06:30"))
        now = self.at("07:00")
        accts = list(self.cfg.accounts.values())
        with CD.edit_board(self.gta, DAY) as bd:
            bd["cards"]["late"] = {"status": "approved", "kind": "clip_of_day"}
        marks = [{"k": "card", "s": "review"}, {"k": "card", "s": "scheduled"}]
        self.assertEqual(CD.cal_items(self.cfg, now, accts), {DAY.isoformat(): {"gaming": marks}})
        self.set(self.gta, {"cards.make": "off"})
        self.assertFalse(CD.any_part_on(self.gta))
        self.assertEqual((CD.summary(self.cfg, now, accts), CD.cal_items(self.cfg, now, accts)), ([], {}))
        self.assertEqual(SOC.cal_items(self.cfg, now, accts), {})
        later = now + timedelta(days=5)
        self.assertEqual(CD.cal_items(self.cfg, later, accts), {})
        self.set(self.gta, {"cards.post": "on"})
        self.assertIn(DAY.isoformat(), CD.cal_items(self.cfg, now, accts))   # any one part on shows them again


class DoctorTest(Base):
    def test_1_what_it_waits_for(self):
        rows = CD.doctor(self.cfg)
        self.assertIn((None, "Gaming cards: off (factory.py social on gaming cards.make; Instagram stories not "
                             "tested: factory.py cards probe gaming); cards.post and cards.story_repost (with its "
                             "rule signed on the Cards tab) start at --practice, then wait for the go-live checklist",
                       ""), rows)
        self.set(self.gta, {"cards.make": "on"}, {"cards.targets": ["facebook_story", "instagram_story"]})
        with mock.patch.object(CD, "_engine_check", return_value=(False, "No module named PIL")):
            rows = CD.doctor(self.cfg)
        no = [r for r in rows if r[0] is False]
        self.assertTrue(any("the engine Python cannot import PIL and fontTools" in r[1] and
                            "pip install --user pillow fonttools" in r[2] for r in no), rows)
        self.assertIn((None, "Instagram stories on Gaming: not tested (factory.py cards probe gaming)", ""), rows)
        # the skill folder is the test's own (cfg_in_tmp), empty: no kit is said, then the kit is put in it
        self.assertTrue(any("Gaming cards: no brand kit 'gta' in the skill" in r[1] for r in no), rows)
        kit = self.gta.skill_dir / "assets" / "brand" / "gta" / "kit.json"
        kit.parent.mkdir(parents=True, exist_ok=True)
        kit.write_text("{}")
        CAP.put_probe(self.gta, "ig_stories", {"ok": False, "kind": "creator", "words": "Creator"})
        with mock.patch.object(CD, "_engine_check", return_value=(True, "")):
            rows = CD.doctor(self.cfg)
        bad = [r for r in rows if r[0] is False]
        self.assertEqual(len(bad), 1, rows)
        self.assertIn("Creator account", bad[0][1])
        self.assertIn("Andrew's call", bad[0][2])

    def test_2_the_probe_and_the_command(self):
        self.grant(self.gta)
        g = FakeGraph().on("POST", rf"/{IG}/media$", FakeGraph.meta_error(
            10, msg="(#10) The user is not an Instagram Business"))
        patch_http(self, graph=g)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            REG.run_cli(self.cfg, "cards", ["probe", "gaming"])
        self.assertIn("Creator account", buf.getvalue())
        self.assertEqual(CAP.probe_result(self.gta, "ig_stories")["value"]["kind"], "creator")
        g.on("POST", rf"/{IG}/media$", {"id": "c42"})
        g.routes.reverse()
        got = API.probe_stories(self.gta)
        self.assertEqual((got["ok"], got["container"]), (True, "c42"))
        self.assertEqual(g.calls[-1]["form"], {"media_type": "STORIES", "upload_type": "resumable",
                                               "access_token": "EAAfakefakefakefake"})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            REG.run_cli(self.cfg, "cards", ["status", "gaming", "--date", DAY.isoformat()])
        self.assertIn("Gaming cards on 2026-11-02 (not made yet)", buf.getvalue())


# ---------------------------------------------------------------------------------------------- the review's fixes
class ReviewFixesTest(Base):
    """2026-10-20 review: each finding that was real, reproduced, then fixed."""

    def setUp(self):
        super().setUp()
        self.grant(self.gta)
        self.g = FakeGraph()
        patch_http(self, graph=self.g)

    def countdown(self, targets=("facebook_story",), post="on"):
        self.set(self.gta, {"cards.make": "on", "cards.post": post},
                 {"cards.kinds": ["countdown"], "cards.targets": list(targets)})
        self.make(self.gta)
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        CD.approve(self.cfg, self.gta, DAY, "countdown-0900", CD.card_hash(c), "Andrew", self.at("07:00"))
        return CD.get_card(self.gta, DAY, "countdown-0900")

    def photo_routes(self):
        self.g.on("POST", rf"/{PAGE}/photos$", lambda c: {"id": f"ph{len(self.g.calls)}"})
        self.g.on("POST", rf"/{PAGE}/photo_stories$", lambda c: {"success": True, "post_id": f"st{len(self.g.calls)}"})

    # 1. Make again never draws over the files a signed card points at
    def test_1_make_again_racing_the_tick_keeps_the_signed_files(self):
        self.countdown(post="practice")
        real = CD._run_engine

        def engine_and_the_tick(spec_path):
            CD._advance(self.cfg, self.gta, DAY, self.at("09:00"))       # the studio tick queues it meanwhile
            spec = json.loads(Path(spec_path).read_text())
            spec["slides"][0]["days"] = 99
            Path(spec_path).write_text(json.dumps(spec))
            return real(spec_path)
        with mock.patch.object(CD, "_run_engine", engine_and_the_tick):
            lines = CD.make(self.cfg, self.gta, DAY, self.at("08:59"), only="countdown-0900", again=True,
                            by="Andrew")
        self.assertTrue(any("kept as signed" in ln for ln in lines), lines)
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(c["status"], "scheduled")
        self.assertEqual(CD.signed_ok(c), "")                            # the signed picture is untouched
        runs = sorted(f.name for f in CD.card_dir(self.gta, DAY, "countdown-0900").iterdir() if f.is_dir())
        self.assertEqual(runs, ["r1"])                                    # the new drawing was dropped

    def test_2_a_remake_swaps_in_a_new_folder_and_drops_the_old(self):
        c = self.countdown()
        old = Path(c["files"]["images"][0]["path"])
        CD.remake(self.cfg, self.gta, DAY, "countdown-0900", "Andrew", self.at("07:05"))
        c2 = CD.get_card(self.gta, DAY, "countdown-0900")
        new = Path(c2["files"]["images"][0]["path"])
        self.assertEqual((old.parent.name, new.parent.name), ("r1", "r2"))
        self.assertFalse(old.parent.exists())
        self.assertEqual((c2["status"], c2["signoff"]), ("ready", None))

    def test_3_a_failed_make_again_leaves_the_signed_card_whole(self):
        self.countdown()
        bad = self.tmp / "bad_engine.py"
        bad.write_text('import json\nprint(json.dumps({"ok": False, "error": "no fonts"}))\nraise SystemExit(1)\n')
        with mock.patch.object(CD, "ENGINE", bad):
            with self.assertRaises(SystemExit):
                CD.remake(self.cfg, self.gta, DAY, "countdown-0900", "Andrew", self.at("07:05"))
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual((c["status"], CD.signed_ok(c)), ("approved", ""))
        self.assertEqual(sorted(f.name for f in CD.card_dir(self.gta, DAY, "countdown-0900").iterdir()
                                if f.is_dir()), ["r1"])

    def test_4_bytes_changed_after_the_last_check_never_upload(self):
        c = self.countdown()
        self.photo_routes()
        img = Path(c["files"]["images"][0]["path"])
        real = CD.still_signed
        n = {"c": 0}

        def check_then_draw_over(cfg, acct, action):
            got = real(cfg, acct, action)
            n["c"] += 1
            if n["c"] == 2:                                              # after the executor's own last check
                img.write_bytes(img.read_bytes() + b"other pixels")
            return got
        with mock.patch.object(CD, "still_signed", check_then_draw_over):
            CD.tick(self.cfg, self.at("09:00"))
        self.assertEqual(self.g.calls, [])                               # the changed bytes never left
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(c["status"], "failed")
        self.assertIn("changed after it was signed", c["outlets"]["facebook_story"]["why"])

    def test_5_each_carousel_child_is_checked_as_it_uploads(self):
        vids = [self.tmp / f"{i}.mp4" for i in (1, 2)]
        for v in vids:
            v.write_bytes(b"video " + v.name.encode())
        shas = [B.sha256_file(v) for v in vids]
        self.g.on("POST", rf"/{IG}/media$", lambda c: {"id": f"c{len(self.g.calls)}"})
        self.g.on("POST", r"rupload\.facebook\.com", {"success": True})
        self.g.on("GET", r"^/c\d+$", {"status_code": "FINISHED"})
        with mock.patch.object(API, "_sleep", lambda s: vids[1].write_bytes(b"changed")):
            self.g.on("GET", r"^/c1$", {"status_code": "IN_PROGRESS"}, times=1)
            self.g.routes.insert(0, self.g.routes.pop())
            with self.assertRaises(SystemExit) as ex:
                API.ig_carousel(self.gta, vids, lambda f: None, shas=shas)
        self.assertIn("2.mp4 changed after it was signed", str(ex.exception))
        ups = [c for c in self.g.calls if "rupload" in c["url"]]
        self.assertEqual([u["data"] for u in ups], [b"video 1.mp4"])

    # 2. an account with no Instagram linked never stops the others
    def test_6_no_instagram_linked_never_ends_the_tick(self):
        write_tokens(self.gta, page_id=PAGE, ig_user_id="")
        self.countdown(targets=("instagram_story",))
        self.set(self.politics, {"cards.make": "on", "cards.post": "on"},
                 {"cards.kinds": ["docket"], "cards.targets": ["facebook_feed"]})
        self.grant(self.politics)
        y = DAY - timedelta(days=1)
        self.posted(self.politics, y, "07:00", "08:45", "10:30")
        self.make(self.politics)
        c = CD.get_card(self.politics, DAY, "docket-1200")
        CD.approve(self.cfg, self.politics, DAY, "docket-1200", CD.card_hash(c), "Andrew", self.at("07:00"))
        write_tokens(self.politics, page_id=PAGE, ig_user_id="")
        self.g.on("POST", rf"/{PAGE}/photos$", lambda c: {"id": f"ph{len(self.g.calls)}"})
        self.g.on("POST", rf"/{PAGE}/feed$", {"id": f"{PAGE}_9"})
        for t in ("09:00", "09:10", "12:00", "12:10"):
            CD.tick(self.cfg, self.at(t))                                 # never raises
        self.assertEqual(CD.get_card(self.politics, DAY, "docket-1200")["status"], "posted")
        g = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(g["status"], "failed")
        self.assertIn("no Instagram account is linked", g["outlets"]["instagram_story"]["why"])
        with self.assertRaises(ValueError):
            API.find(self.gta, "somewhere", datetime.now(timezone.utc))
        with mock.patch.object(API, "find", side_effect=SystemExit("no")):
            with self.assertRaises(RuntimeError):
                CD.find(self.cfg, self.gta, {"target": {"outlet": "facebook_story"}})

    # 3. the day's card posts: signing stops at the cap; a cap is a skip, never red
    def test_7_signing_stops_at_the_days_card_posts(self):
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["countdown"]})
        self.make(self.gta)
        base = CD.get_card(self.gta, DAY, "countdown-0900")
        with CD.edit_board(self.gta, DAY) as bd:
            for hh in ("11:00", "13:00", "15:00", "17:00", "19:00"):
                c = json.loads(json.dumps(base))
                c["id"], c["at"] = f"countdown-{hh.replace(':', '')}", CD._iso(self.cfg.slot_time(DAY, hh))
                bd["cards"][c["id"]] = c
        have = CD.read_board(self.gta, DAY)["cards"]
        seen = {cid: CD.card_hash(c) for cid, c in have.items()}
        words = CD.approve_all(self.cfg, self.gta, DAY, seen, "Andrew", self.at("07:00"))
        self.assertIn("Signed 5: countdown-0900, countdown-1100, countdown-1300, countdown-1500, countdown-1700", words)
        self.assertIn("countdown-1900: Not signed: Facebook takes 5 card posts a day at the most", words)
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-1900")["status"], "ready")

    def test_8_a_full_day_waits_and_is_a_skip_not_a_failure(self):
        self.countdown()
        self.photo_routes()
        for h in (8, 6, 4, 2, 1):                                        # five card posts in the last 24 hours
            OW._bump(self.gta.slug, "facebook", "card_post", False, self.at("09:00") - timedelta(hours=h), False)
        CD.tick(self.cfg, self.at("09:00"))
        self.assertEqual(self.g.calls, [])
        self.assertEqual(len(OW.due(self.cfg, self.gta, self.at("09:00"), runner="studio")), 1)   # it waits
        CD.tick(self.cfg, self.at("18:00"))                                # past its expiry
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(c["status"], "skipped")
        self.assertIn("its time passed", c["outlets"]["facebook_story"]["why"])
        self.assertEqual(CD.health(self.cfg, self.at("18:00"), [self.gta]), ("", ""))

    # 4. a try with no clear answer (a 5xx or a transient error) is looked up, never resent blind (the seam's
    # lost-answer contract, docs/social/README.md): found, the card is posted; not found, a person looks
    def transient_story(self, found: bool):
        self.countdown()

        def story(call):
            raise FakeGraph.meta_error(2, msg="An unexpected error has occurred.", http=500, transient=True)
        when = self.cfg.slot_time(DAY, "09:00").astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
        seen = {"data": [{"post_id": "st2", "creation_time": when, "url": "https://fb/st2"}] if found else []}
        self.g.on("POST", rf"/{PAGE}/photos$", lambda c: {"id": f"ph{len(self.g.calls)}"})
        self.g.on("POST", rf"/{PAGE}/photo_stories$", story)
        self.g.on("GET", rf"/{PAGE}/stories$", seen)
        CD.tick(self.cfg, self.at("09:00"))
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "scheduled")
        item = next(iter(OW._items(self.gta.slug).values()))
        self.assertEqual(item["state"], "sending")                         # no clear answer: looked up first
        self.assertGreater(datetime.fromisoformat(item["expires"]),
                           self.cfg.slot_time(DAY, "09:00") + timedelta(hours=4, minutes=50))
        for t in ("09:10", "12:00", "12:30", "12:40"):
            CD.tick(self.cfg, self.at(t))
        self.assertEqual([p for p in self.g.paths() if p.endswith("photo_stories")], [f"POST /{PAGE}/photo_stories"])
        self.assertIn(f"GET /{PAGE}/stories", self.g.paths())
        return CD.get_card(self.gta, DAY, "countdown-0900")

    def test_9_a_try_with_no_clear_answer_is_looked_up_and_found(self):
        c = self.transient_story(found=True)
        self.assertEqual((c["status"], c["outlets"]["facebook_story"]["post_id"]), ("posted", "st2"))

    def test_9b_not_found_after_no_clear_answer_it_is_never_resent_blind(self):
        c = self.transient_story(found=False)
        self.assertEqual(c["status"], "failed")
        self.assertIn("Not sure it went: look on Facebook", c["outlets"]["facebook_story"]["why"])

    # 5. Stop everything and holds: no Graph read before outward's own check
    def test_10_stop_reads_nothing_from_meta(self):
        self.countdown(targets=("instagram_story",))
        OW.stop(self.cfg, "Andrew", "test")
        CD.tick(self.cfg, self.at("09:00"))
        self.assertEqual(self.g.calls, [])
        OW.start(self.cfg, "Andrew")
        OW.hold(self.cfg, self.gta, "instagram", "publish", 3600, "Meta said wait", now=self.at("09:00"))
        CD.tick(self.cfg, self.at("09:05"))
        self.assertEqual(self.g.calls, [])

    # 8 and 11. a queued card can still be declined or re-timed
    def test_11_decline_a_queued_card(self):
        self.countdown()
        self.photo_routes()
        slot_ready(self, self.gta, DAY, "08:45")
        self.sign_clip(self.gta, DAY, "08:45")
        CD.tick(self.cfg, self.at("09:01"))                              # queued behind the 08:45 Reel
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "scheduled")
        CD.decline(self.cfg, self.gta, DAY, "countdown-0900", ["not_needed"], "", "Andrew")
        CD.tick(self.cfg, self.at("09:20"))
        self.assertEqual(self.g.calls, [])
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(c["status"], "declined")
        self.assertEqual(OW.due(self.cfg, self.gta, self.at("09:30"), runner="studio"), [])

    def test_12_a_card_on_its_way_cannot_be_declined(self):
        self.countdown()
        CD._advance(self.cfg, self.gta, DAY, self.at("09:00"))
        key = f"card:facebook:{DAY}/countdown-0900:facebook_story"
        item = next(iter(OW._items(self.gta.slug).values()))
        OW._set_key(self.gta.slug, key, "sending", item, self.at("09:00"))
        with self.assertRaises(SystemExit) as ex:
            CD.decline(self.cfg, self.gta, DAY, "countdown-0900", [], "", "Andrew")
        self.assertIn("on its way to Facebook story", str(ex.exception))

    # 12. a skipped or failed card can be given a new time and signed again
    def test_13_change_time_brings_a_skipped_card_back(self):
        self.countdown()
        with CD.edit_board(self.gta, DAY) as bd:
            bd["cards"]["countdown-0900"]["status"] = "skipped"
        CD.set_time(self.cfg, self.gta, DAY, "countdown-0900", "18:00", "Andrew", self.at("12:30"))
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual((c["status"], c["signoff"], c["outlets"]), ("ready", None, {}))
        CD.approve(self.cfg, self.gta, DAY, "countdown-0900", CD.card_hash(c), "Andrew", self.at("12:31"))
        self.photo_routes()
        CD.tick(self.cfg, self.at("18:05"))
        self.assertEqual(CD.get_card(self.gta, DAY, "countdown-0900")["status"], "posted")

    # 13. the coach's top clips, on the coach's own scales
    def test_14_which_top_reads_the_coach_out_of_10(self):
        self.set(self.gta, {"cards.story_repost": "practice"})
        CD.sign_rule(self.cfg, self.gta, {"which": "top"}, None, "Andrew", self.at("07:00"))
        self.posted(self.gta, DAY, "10:30", "12:15", "14:00", published=self.cfg.slot_time(DAY, "10:30"))
        coach = {"10:30": {"measured": {"total": 99}, "written": {"scores": {"a": 5, "b": 6, "c": 5, "d": 6, "e": 5}}},
                 "12:15": {"measured": {"total": 100}},
                 "14:00": {"measured": {"total": 30}, "written": {"scores": {"a": 8, "b": 8, "c": 7, "d": 9, "e": 8}}}}
        for k, card in coach.items():
            store.write(store.path(self.gta.slug, "coach", DAY.isoformat(), f"{k.replace(':', '')}.json"), card)
        got = {}
        for k in coach:
            s = B.read(self.gta, DAY)["slots"][k]
            got[k] = CD.story_for(self.cfg, self.gta, DAY, s, "instagram_reels", s["outlets"]["instagram_reels"],
                                  self.at("10:31"))
        self.assertIn("not one of the top clips", got["10:30"])      # written 5.4: under the 7
        self.assertIn("has not written a score", got["12:15"])       # measured only: mechanics never pick the top
        self.assertIn("queued", got["14:00"])
        self.assertIn("scored 7 or more out of 10", CD.rule_words(CD.read_rule(self.gta)))

    # 6 and 14. the countdown on a master frame of yesterday's clip; never another account's brand
    def test_15_the_countdown_never_has_a_frame(self):
        """2026-10-20: the countdown is not about one clip, so it never carries a creator's footage, even with
        yesterday's best clip and its master right here (a fan map's own "32 DAYS TO GO" once sat over our "30")."""
        y = DAY - timedelta(days=1)
        self.posted(self.gta, y, "07:00", "08:45", views={"07:00": 5, "08:45": 50})
        job = "factory-test-job"
        work = self.gta.skill_dir / "work" / job
        self.assertTrue(work.resolve().is_relative_to(self.tmp.resolve()), work)  # never the real skill's work/
        work.mkdir(parents=True, exist_ok=True)
        master = self.tmp / "gta-master.mp4"
        master.write_bytes(b"master")
        slug = B.read(self.gta, y)["slots"]["08:45"]["slug"]
        (work / "slate.json").write_text(json.dumps({"clips": [{"slug": slug, "start": 100.0, "end": 140.0}]}))
        (work / "project.json").write_text(json.dumps({"source": str(master)}))
        with B.edit(self.cfg, self.gta, y) as bd:
            bd["slots"]["08:45"]["job"] = job
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["countdown"]})
        self.make(self.gta)
        self.assertEqual((self.grabs, self.probes), ([], []))      # nothing of the master was even looked at
        spec = FakeEngine.calls(self.eng.parent)[-1]["spec"]
        self.assertNotIn("still", spec["slides"][0])
        c = CD.get_card(self.gta, DAY, "countdown-0900")
        self.assertEqual(c["source"], {"launch": CD.LAUNCH.isoformat(), "art": "ink"})
        for d in (DAY, CD.LAUNCH - timedelta(days=1), CD.LAUNCH):         # every day, launch day too
            card, slides, _ = CD.build_countdown(self.cfg, self.gta, d, self.at("07:00"))[0]
            self.assertEqual([sorted(s) for s in slides], [["date", "days", "layout"]], d)
        self.assertEqual(self.grabs, [])

    def test_15b_the_test_config_never_points_at_a_real_skill(self):
        """cfg_in_tmp moves every account's skill folder into the tmp folder: test_15 once wrote work/<job>/ into
        the live ~/.claude/skills/gaming on every run and left it there."""
        real = Path(os.path.expanduser("~/.claude/skills")).resolve()
        for a in self.cfg.all_accounts.values():
            sd = Path(a.skill_dir).resolve()
            self.assertTrue(sd.is_relative_to(self.tmp.resolve()), (a.slug, sd))
            self.assertFalse(sd.is_relative_to(real), (a.slug, sd))
        self.assertEqual(self.gta.skill_dir.name, "gaming")

    def test_16_a_missing_kit_is_refused_never_borrowed(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("card_engine_t", str(C.ROOT / "render" / "card_engine.py"))
        eng = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(eng)
        skill = self.tmp / "skill"
        (skill / "assets" / "brand" / "gta").mkdir(parents=True)
        (skill / "assets" / "brand" / "gta" / "kit.json").write_text("{}")
        with self.assertRaises(ValueError) as ex:
            eng.Gta(str(skill), "stunts")
        self.assertIn("no brand kit 'stunts'", str(ex.exception))
        self.assertNotIn("card-backdrop", (C.ROOT / "render" / "card_engine.py").read_text())
        self.set(self.gta, {"cards.make": "on"})
        with mock.patch.object(self.gta, "kit", "nokit"), mock.patch.object(CD, "_engine_check",
                                                                              return_value=(True, "")):
            rows = CD.doctor(self.cfg)
        self.assertTrue(any(r[0] is False and "no brand kit 'nokit'" in r[1] for r in rows), rows)

    # 16. the time the card can really go
    def test_17_the_real_time_past_a_reels_gap(self):
        c = self.countdown()
        slot_ready(self, self.gta, DAY, "08:45")
        go, why = CD.go_time(self.cfg, self.gta, c)
        self.assertEqual(go, self.cfg.slot_time(DAY, "09:15").astimezone(timezone.utc))
        self.assertIn("08:45 Reel keeps 30 minutes clear", why)

    # 17. why a card was not made is kept
    def test_18_why_not_made_is_kept(self):
        self.set(self.politics, {"cards.make": "on"}, {"cards.kinds": ["quote", "docket"]})
        self.politics_clip(people=(ROSS, LEE))
        self.make(self.politics)
        sk = CD.read_board(self.politics, DAY)["made"]["skipped"]
        self.assertTrue(any(r["kind"] == "quote" and "no speaker verified by a person" in r["words"] for r in sk), sk)
        self.assertTrue(any(r["kind"] == "docket" and "3 at the least" in r["words"] for r in sk), sk)

    # 20. the 06:30 notice counts cards the tick has not made yet
    def test_19_the_morning_notice_makes_them_first(self):
        self.set(self.gta, {"cards.make": "on"}, {"cards.kinds": ["countdown"]})
        self.assertEqual(CD.morning_line(self.cfg, self.at("06:30")), "1 card to sign")
        n = len(FakeEngine.calls(self.eng.parent))
        CD.tick(self.cfg, self.at("06:36"))
        self.assertEqual(len(FakeEngine.calls(self.eng.parent)), n)        # made once


if __name__ == "__main__":
    unittest.main()
