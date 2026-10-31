import os, tempfile
os.environ["CLIP_FACTORY_OFFLINE"] = "1"
os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp(prefix="clipfactory-copy-"))
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import copytext as T  # noqa: E402

POLITICS = """# SLOT 07:00
# Lines starting with # are notes. Do not paste them.

TITLE
Patel would not answer

CAPTION
Senate Judiciary, September 15 2026.

Full hearing: https://example.gov/x

(no hashtags on facebook, on purpose)
"""


class CopyNotes(unittest.TestCase):
    def test_the_hand_posting_note_never_reaches_a_caption(self):
        w = T.for_platform(POLITICS, "facebook_reels")
        self.assertNotIn("on purpose", w["caption"])
        self.assertTrue(w["caption"].endswith("https://example.gov/x"))
        self.assertNotIn("on purpose", T.for_platform(POLITICS.replace("facebook", "instagram"), "instagram_reels")["caption"])
        self.assertEqual(T.for_platform(POLITICS, "youtube_shorts")["title"], "Patel would not answer")

    def test_words_that_merely_mention_it_stay(self):
        w = T.for_platform("TITLE\nx\n\nCAPTION\nWe post with no hashtags on facebook, on purpose, always.\n", "facebook_reels")
        self.assertIn("on purpose", w["caption"])


if __name__ == "__main__":
    unittest.main()
