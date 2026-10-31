import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""THE PHONE PUSH (clipfactory/phone.py). 2026-10-29 09:50 MT the breaking ask's three-line message went into the
image PUT's Message header raw and urllib refused it ("Invalid header value"), so Andrew got no ping. Every header
here is checked the way http.client checks it, against a mocked urlopen: nothing is ever pushed to the real topic."""
import base64
import http.client
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import phone  # noqa: E402

FAKE = {"on": True, "server": "https://ntfy.invalid", "topic": "politics-ask-test", "reply": "politics-reply-test"}
ASK = "Trump says two Dan Sullivans on Alaska ballots is voter fraud.\nPosts the moment it's made, an extra post.\nPost it?"


def _decode(v: str) -> str:
    if v.startswith("=?UTF-8?B?") and v.endswith("?="):
        return base64.b64decode(v[10:-2]).decode()
    return v


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"id": "mock1"}'


class _Online(unittest.TestCase):
    """The push code returns early while CLIP_FACTORY_OFFLINE is set; these tests mock urlopen, so each one runs with it
    cleared for its own run only (the module itself goes offline first, as the state guard asks)."""
    def setUp(self):
        p = mock.patch.dict(os.environ, {"CLIP_FACTORY_OFFLINE": ""})
        p.start()
        self.addCleanup(p.stop)


class PhoneHeaders(_Online):
    def test_one_line_ascii_stays_as_it_is(self):
        self.assertEqual(phone._h("Post it?"), "Post it?")

    def test_line_breaks_and_non_ascii_are_encoded_and_read_back(self):
        for v in (ASK, "a\r\nb", "café"):
            out = phone._h(v)
            self.assertTrue(out.startswith("=?UTF-8?B?"), v)
            self.assertNotIn("\n", out)
            self.assertEqual(_decode(out), v)

    def test_the_breaking_ask_with_a_picture_goes_out(self):
        seen = []

        def fake_urlopen(req, timeout=None):
            for k, v in req.header_items():  # the check urllib's http.client makes before sending
                self.assertTrue(http.client._is_legal_header_name(k.encode()), k)
                self.assertFalse(http.client._is_illegal_header_value(v.encode("latin-1")), (k, v))
            seen.append(req)
            return _Resp()

        with tempfile.TemporaryDirectory() as d:
            img = Path(d) / "card.jpg"
            img.write_bytes(b"\xff\xd8 not really a jpeg")
            with mock.patch.object(phone, "settings", return_value=FAKE), \
                    mock.patch.object(phone.urllib.request, "urlopen", fake_urlopen):
                ok, why = phone.send("BREAKING: Politics", ASK, image=img,
                                     buttons=[("Yes", "breaking yes x"), ("No", "breaking no x")])
        self.assertEqual((ok, why), (True, "mock1"))
        self.assertEqual(seen[0].get_method(), "PUT")
        self.assertEqual(_decode(seen[0].get_header("Message")), ASK)


def _ntfy_split(s: str, sep: str) -> list:
    """Split the way ntfy reads the Actions header: on sep, never inside double or single quotes."""
    out, cur, quote = [], "", ""
    for ch in s:
        if quote:
            cur += ch
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            cur, quote = cur + ch, ch
        elif ch == sep:
            out, cur = out + [cur.strip()], ""
        else:
            cur += ch
    return out + [cur.strip()]


class PhoneButtons(_Online):
    """2026-10-30: the real labels have commas ("Yes, post it", "Yes, make it"); ntfy read "post it" as the button's
    URL and refused every ask with 400. The old test above used "Yes", so it never saw it."""

    def test_labels_with_commas_stay_one_button(self):
        real = [("Yes, post it", "breaking yes politics 2026-10-30 ZNMT8kWzR_Y"),
                ("Yes, make it", "news yes politics 2026-10-30 n1"), ("No", "breaking no x"),
                ('Say "go"', "go")]
        acts = _ntfy_split(phone._actions(FAKE, real), ";")
        self.assertEqual(len(acts), len(real))
        for act, (label, body) in zip(acts, real):
            f = _ntfy_split(act, ",")
            self.assertEqual(f[0], "http")
            self.assertEqual(f[1][1:-1], label)
            self.assertEqual(f[2], "https://ntfy.invalid/politics-reply-test")
            self.assertEqual(dict(x.split("=", 1) for x in f[3:])["body"][1:-1], body)


if __name__ == "__main__":
    unittest.main()
