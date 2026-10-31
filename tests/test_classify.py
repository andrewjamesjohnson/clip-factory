import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The Inbox's tags and suggestions (classify.py), 2026-10-20: a fixed corpus of 60 comments, 30 for Gaming and 30
# for the Politics, each with the tags it must get and whether it is suggested for hiding (spam, toxic or not).
# Disagreement and rudeness are never suggested for hiding on either account; only spam, slurs and threats are. A
# person we know may post a link; a stranger's link is spam. Words only: no state, no network.

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clipfactory import classify as K      # noqa: E402

Q, SRC, FULL, WHO, WHEN, WHICH, P2, PR, STOP = ("question", "asks_source", "asks_full", "asks_who", "asks_when",
                                                "asks_which", "asks_part2", "praise", "stop")

GTA = [  # (words, tags, suggestion kind)
    ("source?", [Q, SRC], ""),
    ("whose stream is this", [Q, SRC, WHO], ""),
    ("who is the streamer", [Q, SRC, WHO], ""),
    ("when does gta 6 come out", [Q, WHEN], ""),
    ("GTA 6 release date?", [Q, WHEN], ""),
    ("part 2 please", [Q, P2], ""),
    ("need part 2", [Q, P2], ""),
    ("full stream?", [Q, FULL], ""),
    ("where can i watch the full vod", [Q, FULL], ""),
    ("what server is this", [Q, WHICH], ""),
    ("W", [PR], ""),
    ("this is fire \U0001F525", [PR], ""),
    ("lmao he crashed out", [PR], ""),
    ("who cares lol", [PR], ""),
    ("is this real", [Q], ""),
    ("link to the stream", [Q, SRC], ""),
    ("bro this game is trash", [], ""),
    ("you're an idiot", [], ""),                      # rude, never hidden for it
    ("kys", [], "toxic"),
    ("free shark cards generator in my bio", [], "spam"),
    ("check out my channel for more gta", [], "spam"),
    ("visit gtamods.xyz now", [], "spam"),
    ("First", [], ""),
    ("nobody:", [], ""),
    ("stop", [STOP], ""),
    ("what car is that", [Q, WHICH], ""),
    ("that's crazy", [], ""),
    ("how did he do that?", [Q], ""),
    ("goat streamer", [PR], ""),
    ("GTA 6 comes out november 19", [], ""),          # telling, not asking
]

POLITICS = [
    ("source?", [Q, SRC], ""),
    ("what hearing is this?", [Q, SRC, WHICH], ""),
    ("who is that senator", [Q, WHO], ""),
    ("full video please", [Q, FULL], ""),
    ("where can I watch the whole hearing", [Q, FULL], ""),
    ("when was this hearing", [Q, WHEN], ""),
    ("which committee is this", [Q, WHICH], ""),
    ("This senator is a liar and an idiot", [], ""),  # disagreement is never toxic on the Politics
    ("Both parties are corrupt", [], ""),
    ("Well said", [PR], ""),
    ("thank you for posting this", [PR], ""),
    ("he should be hanged for treason", [], "toxic"),  # a threat of violence is
    ("I will find you", [], "toxic"),
    ("you n1gger", [], "toxic"),
    ("Crypto investment with my account manager on WhatsApp", [], "spam"),
    ("check my page for real news", [], "spam"),
    ("www.truthnews.com has the full story", [], "spam"),
    ("stop", [STOP], ""),
    ("unsubscribe", [STOP], ""),
    ("what's her name?", [Q, WHO], ""),
    ("is this new or old", [Q, WHEN], ""),
    ("what network is this from", [Q, SRC, WHICH], ""),
    ("This is a disgrace", [], ""),
    ("Vote them all out", [], ""),
    ("Part 2?", [Q, P2], ""),
    ("I disagree with every word", [], ""),
    ("The senator is wrong about crypto", [], ""),    # crypto talk is politics here, not spam
    ("Bitcoin to the moon", [], ""),
    ("who was the witness", [Q, WHO], ""),
    ("great clip", [PR], ""),
]


class CorpusTest(unittest.TestCase):
    def test_1_the_corpus_is_sixty(self):
        self.assertEqual((len(GTA), len(POLITICS)), (30, 30))

    def test_2_gta_tags_and_suggestions(self):
        for words, tags, kind in GTA:
            with self.subTest(words=words):
                self.assertEqual(K.tags(words, "gta"), tags)
                got = K.suggest(words, {"known": False}, [], "gta")
                self.assertEqual(got["kind"], kind)
                self.assertEqual(got["hide"], bool(kind))

    def test_3_politics_tags_and_suggestions(self):
        for words, tags, kind in POLITICS:
            with self.subTest(words=words):
                self.assertEqual(K.tags(words, "politics"), tags)
                got = K.suggest(words, {"known": False}, [], "politics")
                self.assertEqual(got["kind"], kind)
                self.assertEqual(got["hide"], bool(kind))

    def test_4_tags_come_in_order_and_from_the_list(self):
        for words, _, _ in GTA + POLITICS:
            for maker in ("gta", "politics", ""):
                got = K.tags(words, maker)
                self.assertEqual(got, [t for t in K.TAGS if t in got])


class SuggestTest(unittest.TestCase):
    def test_1_a_known_person_may_post_a_link(self):
        words = "the full hearing is on www.c-span.org"
        self.assertEqual(K.suggest(words, {"known": False})["why"], "a link from someone new")
        self.assertFalse(K.suggest(words, {"known": True})["hide"])

    def test_2_the_same_words_three_times(self):
        self.assertFalse(K.suggest("first", {"known": True}, ["first"])["hide"])
        got = K.suggest("First", {"known": True}, ["first", "first!"])
        self.assertEqual((got["kind"], got["why"]), ("spam", "the same words three times"))

    def test_3_our_own_words_are_never_suggested(self):
        self.assertFalse(K.suggest("check out my channel", {"is_us": True})["hide"])

    def test_4_disagreement_and_insults_are_never_suggested(self):
        for words in ("you are an idiot", "this guy is a clown", "worst take ever", "shut up", "liar",
                      "Impeach him now", "This is propaganda"):
            for maker in ("gta", "politics"):
                self.assertFalse(K.suggest(words, {"known": False}, [], maker)["hide"], words)

    def test_5_stop_is_the_start_of_the_message(self):
        for words in ("STOP", "Stop.", "dont message me", "Don’t message me again", "leave me alone pls",
                      "no more messages", "unsubscribe"):
            self.assertTrue(K.is_stop(words), words)
            self.assertIn("stop", K.tags(words))
        for words in ("please dont stop posting", "can't stop laughing"):
            self.assertFalse(K.is_stop(words), words)

    def test_7_politics_news_about_apps_and_money_is_not_spam(self):
        # review 2026-10-20: bare "Telegram", "WhatsApp", "forex" or "Cash App" hid real Politics questions
        for words in ("who is the senator asking about the TikTok and Telegram ban?",
                      "Is this the hearing about WhatsApp encryption?",
                      "what hearing was this? forex manipulation case?",
                      "Cash App fined by the CFPB, finally", "the forex case from 2019-2024?",
                      "OnlyFans testimony was wild", "which committee had the hacked account hearing"):
            for known in (False, True):
                got = K.suggest(words, {"known": known}, [], "politics")
                self.assertFalse(got["hide"], words)
        self.assertIn("asks_which", K.tags("what hearing was this? forex manipulation case?", "politics"))
        for words in ("Earn big with forex, message me on WhatsApp +1 555 123 4567", "add me on telegram @fxking",
                      "dm me on whatsapp for signals", "my account manager is on telegram, contact him @mgr_jane",
                      "Cash App me 555 123 4567"):
            got = K.suggest(words, {"known": True}, [], "politics")
            self.assertEqual((got["hide"], got["kind"]), (True, "spam"), words)

    def test_6_empty_words(self):
        self.assertEqual(K.tags(""), [])
        self.assertEqual(K.suggest(None, None)["hide"], False)


if __name__ == "__main__":
    unittest.main()
