import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Natural answers (answers.py), 2026-10-20: what a reply may say about a clip, only from what is on disk. GTA: the
# board's host, source title and link at the clip's own second, days to GTA 6 from kit.json; the Politics: the job's
# project.json, the hearing from cast.json, the speaker only from a cast a person verified (one person, or the quote
# inside that person's name card), never a provisional or bot-written one. A missing fact gives nothing. Throwaway
# folders only; nothing reaches the network.

import json
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clipfactory import answers as AN          # noqa: E402
from clipfactory import direct as D            # noqa: E402
from clipfactory import store                  # noqa: E402
from social_fakes import cfg_in_tmp            # noqa: E402

DAY = date(2031, 7, 7)
NOW = datetime(2031, 7, 8, 16, 0, tzinfo=timezone.utc)          # 10:00 in Denver, the day after
GTA_URL = "https://www.youtube.com/watch?v=LIugtEjZl_8"
JOB = "ABC Wright This Week 07.06.31"
HEARING_JOB = "Senate Banking Warren Johnson 07.05.31"
VERIFIED = "Andrew, in Clip Review's Casts (2031-07-06 06:55): looked at the stills, this is Chris Wright"


def _w(p: Path, data) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return p


class World:
    """A Gaming and a Politics with one posted clip each, their jobs and kit on disk, in a tmp folder."""

    def __init__(self, test, launch: str = "2031-08-01"):
        self.test = test
        self.cfg = cfg_in_tmp(test)
        self.tmp = test.tmp
        self.gta = self.cfg.account("gaming")
        self.politics = self.cfg.account("politics")
        self.gta.skill_dir = self.tmp / "skills" / "gaming"
        self.politics.skill_dir = self.tmp / "skills" / "viral-clip-engine"
        _w(self.gta.skill_dir / "assets" / "brand" / "gta" / "kit.json", {"launch": {"date": launch}})
        self.gta_slot()
        self.politics_job()

    # -------------------------------------------------------------------------------------------- GTA
    def gta_slot(self, slot: str = "08:45", day: date = DAY, url: str = GTA_URL, pick=(287.32, 324.32),
                 fc_done: bool = True, slug: str = "a-house-like-this", ig: str = "IG1", fb: str = "FB1") -> dict:
        at = f"{day}T14:45:00+00:00"
        s = {"slot": slot, "publish_at": f"{day}T{slot}:00-06:00", "status": "scheduled", "slug": slug,
             "host": "MrBossFTW", "source_url": url, "source_title": "The biggest update we have gotten",
             "pick_span": list(pick) if pick else None,
             "outlets": {"instagram_reels": {"post_id": ig, "provider": "direct", "published_at": at,
                                             "url": f"https://www.instagram.com/reel/{ig}/"},
                         "facebook_reels": {"post_id": fb, "provider": "direct", "published_at": at,
                                            "url": f"https://www.facebook.com/reel/{fb}",
                                            "social": {"first_comment": {"state": "done" if fc_done else "practice"}}}}}
        p = store.path(self.gta.slug, f"{day}.json")
        bd = store.read(p, {}) or {"slots": {}}
        bd.setdefault("slots", {})[slot] = s
        store.write(p, bd)
        return s

    def fc_words(self, words: dict, slot: str = "08:45", day: date = DAY, done=("facebook_reels",
                                                                                "instagram_reels")) -> None:
        """The slot's first comment words per outlet (its extras file), and each listed outlet's marker done."""
        from clipfactory import extras as EX
        f = _w(EX.path(self.gta, day, slot), {"v": 1, "first_comment": {o: {"text": t, "from": "default"}
                                                                        for o, t in words.items()}})
        p = store.path(self.gta.slug, f"{day}.json")
        bd = store.read(p, {})
        s = bd["slots"][slot]
        s["extras"] = {"path": str(f), "sha256": "x"}
        for o in done:
            s["outlets"][o].setdefault("social", {})["first_comment"] = {"state": "done"}
        store.write(p, bd)

    def gta_post(self, outlet: str = "instagram_reels", slot: str = "08:45", day: date = DAY, pid: str = "IG1") -> dict:
        return {"platform_id": pid, "outlet": outlet, "board": day.isoformat(), "slot": slot,
                "url": f"https://www.instagram.com/reel/{pid}/", "title": "A house like this"}

    # -------------------------------------------------------------------------------------------- Politics
    def politics_job(self, job: str = JOB, people=None, hearing=None, cards=None, quote="President Trump is always "
                   "working to move them in the direction to have that kind of a deal.", slug="wright-ceasefire",
                   slot="17:30", day: date = DAY, srt: str = "", fmt="interview", pid="LIG1") -> dict:
        jd = self.politics.account_dir / "Jobs" / job
        _w(jd / "project.json", {"source_title": "This Week with George Stephanopoulos",
                                 "source_url": "https://abcnews.example/this-week-full", "source_date": "2031-07-06",
                                 "source_channel": "ABC News", "format": fmt})
        _w(jd / "slate.json", {"clips": [{"slug": slug, "format": fmt, "quote": quote,
                                          "cards": [{"who": p["id"]} for p in (people or [])][:1]}]})
        cast = {"people": people if people is not None else
                [{"id": "wright", "name": "Chris Wright", "title": "Energy Secretary", "verified": VERIFIED}]}
        if hearing:
            cast["hearing"] = hearing
        _w(jd / "cast.json", cast)
        if cards is not None:
            _w(jd / "clips" / f"01-{slug}.cards.json", {"clip": f"01-{slug}", "cards": cards})
        drafts = self.tmp / "drafts" / f"Politics {day}" / f"{slot.replace(':', '')} {slug}"
        clip = drafts / f"01-{slug}.mp4"
        if srt:
            _w(clip.with_suffix(".srt"), srt)
        at = f"{day}T23:30:00+00:00"
        s = {"slot": slot, "publish_at": f"{day}T{slot}:00-06:00", "status": "scheduled", "slug": slug,
             "host": "ABC News", "rights": "rebroadcast", "cast_check": {"job": job, "source_title": "This Week"},
             "clip": {"path": str(clip), "sha256": "x", "seconds": 72.6},
             "outlets": {"instagram_reels": {"post_id": pid, "provider": "direct", "published_at": at,
                                             "url": f"https://www.instagram.com/reel/{pid}/"}}}
        p = store.path(self.politics.slug, f"{day}.json")
        bd = store.read(p, {}) or {"slots": {}}
        bd.setdefault("slots", {})[slot] = s
        store.write(p, bd)
        return s

    def politics_post(self, slot: str = "17:30", day: date = DAY, pid: str = "LIG1") -> dict:
        return {"platform_id": pid, "outlet": "instagram_reels", "board": day.isoformat(), "slot": slot,
                "url": f"https://www.instagram.com/reel/{pid}/", "title": "Wright on the ceasefire"}

    def item(self, acct, post: dict, text: str, who: str = "111", name: str = "sam", tags=None, iid: str = "",
             at: datetime = NOW, platform: str = "instagram", kind: str = "comment") -> dict:
        n = iid or f"{who}{abs(hash((text, at.isoformat(), post.get('platform_id')))) % 10 ** 8}"
        pf = {"instagram": "ig", "facebook": "fb", "youtube": "yt"}[platform]
        return {"id": f"{pf}:{n}", "platform": platform, "kind": kind, "platform_comment_id": n, "parent_id": None,
                "post": dict(post), "author": {"key": f"{platform}:{who}", "id": who, "name": name, "is_us": False},
                "text": text, "at": at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                "tags": list(tags) if tags is not None else None, "hidden": False, "can_reply_privately": True,
                "private_until": None}


def no_network(test):
    p = mock.patch.object(D, "_http", side_effect=AssertionError("no network in answers"))
    p.start()
    test.addCleanup(p.stop)


class GtaFacts(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        no_network(self)

    def test_board_facts_with_the_clip_second(self):
        fx = AN.facts(self.w.cfg, self.w.gta, self.w.gta_post(), NOW)
        self.assertEqual(fx["host"], "MrBossFTW")
        self.assertEqual(fx["source_title"], "The biggest update we have gotten")
        self.assertEqual(fx["source_url"], GTA_URL + "&t=287s")
        self.assertEqual(fx["kind"], "video")
        self.assertEqual(fx["gta6_date"], "August 1, 2031")
        self.assertEqual(fx["days_to_gta6"], "24 days")
        self.assertNotIn("first_comment", fx)              # Instagram's first comment has not gone

    def test_first_comment_only_from_its_marker(self):
        fx = AN.facts(self.w.cfg, self.w.gta, self.w.gta_post("facebook_reels", pid="FB1"), NOW)
        self.assertNotIn("first_comment", fx)              # done, but no words with a link on disk
        self.w.fc_words({"facebook_reels": "Full video from MrBossFTW: " + GTA_URL + "&t=287s",
                         "instagram_reels": "From MrBossFTW's stream on YouTube."})
        fx = AN.facts(self.w.cfg, self.w.gta, self.w.gta_post("facebook_reels", pid="FB1"), NOW)
        self.assertEqual(fx["first_comment"], "in the first comment")
        self.w.gta_slot(fc_done=False)
        fx = AN.facts(self.w.cfg, self.w.gta, self.w.gta_post("facebook_reels", pid="FB1"), NOW)
        self.assertNotIn("first_comment", fx)

    def test_instagram_first_comment_has_no_link_so_it_is_never_said(self):
        # review 10.20: Instagram's first comment carries no link; "the link is in the first comment" was false there
        self.w.fc_words({"facebook_reels": "Full video from MrBossFTW: " + GTA_URL,
                         "instagram_reels": "From MrBossFTW's stream on YouTube."})
        fx = AN.facts(self.w.cfg, self.w.gta, self.w.gta_post(), NOW)
        self.assertNotIn("first_comment", fx)
        it = self.w.item(self.w.gta, self.w.gta_post(), "full video?", tags=["question", "asks_full"])
        got = AN.suggest(self.w.cfg, self.w.gta, it, NOW)
        self.assertNotIn("first comment", got["text"])
        self.assertEqual(got["text"], "@sam the full video is from MrBossFTW: The biggest update we have gotten.")

    def test_titles_lose_hashtags_and_dashes(self):
        p = store.path(self.w.gta.slug, f"{DAY}.json")
        bd = store.read(p, {})
        bd["slots"]["08:45"]["source_title"] = "GTA 6 TRAILER 2 REACTION \u2014 insane!!! #gta6 #shorts"
        store.write(p, bd)
        fx = AN.facts(self.w.cfg, self.w.gta, self.w.gta_post(), NOW)
        self.assertEqual(fx["source_title"], "GTA 6 TRAILER 2 REACTION, insane!")

    def test_part2_suggestion_promises_nothing(self):
        it = self.w.item(self.w.gta, self.w.gta_post(), "part 2?", tags=["asks_part2"])
        got = AN.suggest(self.w.cfg, self.w.gta, it, NOW)["text"]
        self.assertNotIn("on the way", got)
        self.assertIn("If there is a part 2", got)

    def test_t_replaced_not_doubled(self):
        self.w.gta_slot(url=GTA_URL + "&t=12s", pick=(90, 120))
        fx = AN.facts(self.w.cfg, self.w.gta, self.w.gta_post(), NOW)
        self.assertEqual(fx["source_url"], GTA_URL + "&t=90s")
        self.assertEqual(AN._with_t("https://youtu.be/LIugtEjZl_8", 30), "https://youtu.be/LIugtEjZl_8?t=30s")

    def test_after_launch_no_countdown(self):
        w = self.w
        _w(w.gta.skill_dir / "assets" / "brand" / "gta" / "kit.json", {"launch": {"date": "2031-07-01"}})
        fx = AN.facts(w.cfg, w.gta, w.gta_post(), NOW)
        self.assertNotIn("days_to_gta6", fx)
        it = w.item(w.gta, w.gta_post(), "when does it come out", tags=["asks_when"])
        self.assertIsNone(AN.resolve(w.cfg, w.gta, it, "asks_when", NOW))

    def test_no_kit_no_release_date(self):
        (self.w.gta.skill_dir / "assets" / "brand" / "gta" / "kit.json").unlink()
        fx = AN.facts(self.w.cfg, self.w.gta, self.w.gta_post(), NOW)
        self.assertNotIn("gta6_date", fx)

    def test_render_and_missing(self):
        fx = AN.item_facts(self.w.cfg, self.w.gta, self.w.item(self.w.gta, self.w.gta_post(), "source?"), NOW)
        self.assertEqual(AN.render("{mention} this is {host}.", fx), "@sam this is MrBossFTW.")
        self.assertIsNone(AN.render("{mention} that is {speaker}.", fx))
        self.assertIsNone(AN.render("{mention} {nonsense}", fx))

    def test_resolve_and_suggest(self):
        it = self.w.item(self.w.gta, self.w.gta_post(), "whose stream is this?", tags=["question", "asks_who"])
        self.assertEqual(AN.resolve(self.w.cfg, self.w.gta, it, "asks_who", NOW),
                         "@sam that is MrBossFTW, from The biggest update we have gotten.")
        got = AN.suggest(self.w.cfg, self.w.gta, it, NOW)
        self.assertEqual(got["intent"], "asks_who")
        self.assertIn("who that is", got["why"])
        self.assertNotIn("http", got["text"])

    def test_unknown_post_gives_nothing(self):
        fx = AN.facts(self.w.cfg, self.w.gta, {"platform_id": "nope", "board": "2031-07-01", "slot": "07:00"}, NOW)
        self.assertNotIn("host", fx)
        it = self.w.item(self.w.gta, {"platform_id": "nope"}, "source?", tags=["asks_source"])
        self.assertIsNone(AN.resolve(self.w.cfg, self.w.gta, it, "asks_source", NOW))
        self.assertIsNone(AN.suggest(self.w.cfg, self.w.gta, it, NOW))

    def test_source_key(self):
        s = self.w.gta_slot(url="https://youtu.be/LIugtEjZl_8?t=40")
        self.assertEqual(AN.source_key(self.w.cfg, self.w.gta, s, str(DAY)), "yt:LIugtEjZl_8")
        self.assertEqual(AN.post_source_key(self.w.cfg, self.w.gta, self.w.gta_post()), "yt:LIugtEjZl_8")


class PoliticsFacts(unittest.TestCase):
    def setUp(self):
        self.w = World(self)
        no_network(self)

    def test_project_facts_and_verified_one_person(self):
        fx = AN.facts(self.w.cfg, self.w.politics, self.w.politics_post(), NOW)
        self.assertEqual(fx["host"], "ABC News")
        self.assertEqual(fx["source_title"], "This Week with George Stephanopoulos")
        self.assertEqual(fx["source_url"], "https://abcnews.example/this-week-full")
        self.assertEqual(fx["date"], "July 6, 2031")
        self.assertEqual(fx["kind"], "interview")
        self.assertEqual(fx["speaker"], "Chris Wright")
        self.assertEqual(fx["speaker_title"], "Energy Secretary")
        it = self.w.item(self.w.politics, self.w.politics_post(), "who is that", tags=["asks_who"])
        self.assertEqual(AN.resolve(self.w.cfg, self.w.politics, it, "asks_who", NOW),
                         "@sam that is Chris Wright, Energy Secretary.")

    def test_provisional_never_named(self):
        self.w.politics_job(people=[{"id": "wright", "name": "Chris Wright", "title": "Energy Secretary",
                                   "provisional": {"named_as": "Chris Wright", "by": "politics bot"}}])
        fx = AN.facts(self.w.cfg, self.w.politics, self.w.politics_post(), NOW)
        self.assertNotIn("speaker", fx)
        self.assertIn("speaker", json.loads(fx["_why"]))
        it = self.w.item(self.w.politics, self.w.politics_post(), "who is that", tags=["asks_who"])
        self.assertIsNone(AN.resolve(self.w.cfg, self.w.politics, it, "asks_who", NOW))

    def test_bot_written_verified_is_not_verified(self):
        self.w.politics_job(people=[{"id": "wright", "name": "Chris Wright", "title": "Energy Secretary",
                                   "verified": "the politics bot checked the frames"}])
        self.assertNotIn("speaker", AN.facts(self.w.cfg, self.w.politics, self.w.politics_post(), NOW))

    def test_multi_person_needs_the_quote_in_a_name_card(self):
        people = [{"id": "warren", "name": "Elizabeth Warren", "title": "Senator", "verified": "Andrew checked"},
                  {"id": "johnson", "name": "Ron Johnson", "title": "Senator", "verified": "Andrew checked"}]
        srt = ("1\n00:00:00,000 --> 00:00:03,000\nThank you, Chair\n\n2\n00:00:12,000 --> 00:00:14,000\n"
               "There are options in\n\n3\n00:00:14,000 --> 00:00:16,000\nthe marketplace for sure\n")
        cards = [{"name": "Elizabeth Warren", "who": "warren", "shot": [0.0, 10.0, 0]},
                 {"name": "Ron Johnson", "who": "johnson", "shot": [10.0, 30.0, 1]}]
        self.w.politics_job(job=HEARING_JOB, people=people, cards=cards, srt=srt, fmt="hearing",
                          quote="There are options in the marketplace for sure.",
                          hearing={"body": "Senate Banking Committee", "chamber": "Senate", "date": "2031-07-05"})
        fx = AN.facts(self.w.cfg, self.w.politics, self.w.politics_post(), NOW)
        self.assertEqual(fx["speaker"], "Ron Johnson")
        self.assertEqual(fx["hearing"], "Senate Banking Committee")
        self.assertEqual(fx["hearing_date"], "July 5, 2031")
        self.assertEqual(fx["kind"], "hearing")

    def test_multi_person_quote_not_placed_gives_no_speaker(self):
        people = [{"id": "warren", "name": "Elizabeth Warren", "title": "Senator", "verified": "Andrew checked"},
                  {"id": "johnson", "name": "Ron Johnson", "title": "Senator", "verified": "Andrew checked"}]
        self.w.politics_job(job=HEARING_JOB, people=people, cards=[], srt="", fmt="hearing")
        fx = AN.facts(self.w.cfg, self.w.politics, self.w.politics_post(), NOW)
        self.assertNotIn("speaker", fx)

    def test_multi_person_quote_in_an_unverified_card(self):
        people = [{"id": "warren", "name": "Elizabeth Warren", "verified": "Andrew checked"},
                  {"id": "johnson", "name": "Ron Johnson", "provisional": {"by": "bot"}}]
        srt = "1\n00:00:12,000 --> 00:00:16,000\nThere are options in the marketplace\n"
        cards = [{"who": "warren", "shot": [0.0, 10.0, 0]}, {"who": "johnson", "shot": [10.0, 30.0, 1]}]
        self.w.politics_job(job=HEARING_JOB, people=people, cards=cards, srt=srt,
                          quote="There are options in the marketplace for sure.")
        self.assertNotIn("speaker", AN.facts(self.w.cfg, self.w.politics, self.w.politics_post(), NOW))

    def test_missing_job_gives_none(self):
        p = store.path(self.w.politics.slug, f"{DAY}.json")
        bd = store.read(p, {})
        bd["slots"]["17:30"].pop("cast_check")
        store.write(p, bd)
        fx = AN.facts(self.w.cfg, self.w.politics, self.w.politics_post(), NOW)
        self.assertNotIn("source_title", fx)
        it = self.w.item(self.w.politics, self.w.politics_post(), "where is this from", tags=["asks_source"])
        self.assertIsNone(AN.resolve(self.w.cfg, self.w.politics, it, "asks_source", NOW))

    def test_neutral_defaults(self):
        it = self.w.item(self.w.politics, self.w.politics_post(), "source?", tags=["asks_source"])
        got = AN.resolve(self.w.cfg, self.w.politics, it, "asks_source", NOW)
        self.assertEqual(got, "@sam this is from ABC News, July 6, 2031: This Week with George Stephanopoulos.")
        from clipfactory import rules as RU
        for temps in AN.DEFAULTS["politics"].values():
            for t in temps:
                self.assertIsNone(RU.OPINION.search(t), t)
                self.assertNotIn("!", t)

    def test_day_words(self):
        self.assertEqual(AN.day_words("2031-07-06"), "July 6, 2031")
        self.assertEqual(AN.day_words("10.18.26"), "October 18, 2026")
        self.assertEqual(AN.day_words(""), "")
        self.assertEqual(AN.day_words("2031-13-40"), "")

    def test_source_key_is_the_job(self):
        s = (store.read(store.path(self.w.politics.slug, f"{DAY}.json"), {}) or {})["slots"]["17:30"]
        self.assertEqual(AN.source_key(self.w.cfg, self.w.politics, s, str(DAY)), f"job:{JOB}")


if __name__ == "__main__":
    unittest.main()
