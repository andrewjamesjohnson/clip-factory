import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# A clip's extras (extras.py): its first comment, Instagram poll and trial flag in their own file under state/, signed
# with the clip only when the slot points at one. A clip without extras keeps the exact signature it always had; an
# edit after signing, or a file gone, stops it; Approve takes what is on disk; a new clip or Make again clears them;
# the reel's container gets the poll and trial fields only when switched on and signed. Throwaway folders only.

import hashlib
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

from clipfactory import board as B          # noqa: E402
from clipfactory import collect as K        # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import direct as D         # noqa: E402
from clipfactory import extras as EX        # noqa: E402
from clipfactory import outlets as O        # noqa: E402
from clipfactory import registry as REG     # noqa: E402
from clipfactory import signoff as S        # noqa: E402
from clipfactory import social as SOC       # noqa: E402

DAY = date(2031, 7, 8)
FC = "Full video from Creator: https://www.youtube.com/watch?v=abc&t=61s"


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def old_content_hash(slot: dict) -> str:
    """board.content_hash as it was at d621ba7, before extras: every signature made before must stay true."""
    parts = {
        "slot": slot["slot"],
        "publish_at": slot["publish_at"],
        "clip": slot.get("clip", {}).get("sha256"),
        "copy": {k: v.get("sha256") for k, v in sorted(slot.get("copy", {}).items())},
    }
    if slot.get("cuts"):
        parts["cuts"] = {k: v.get("sha256") for k, v in sorted(slot["cuts"].items())}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


class ExtrasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-extras-"))
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
        self.who = self.cfg.approvers[0]
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

    def deliver(self, k: str, slug: str = "") -> None:
        root = self.gta.delivered_dirs(DAY)[0]
        d = root / f"{k.replace(':', '')} {slug or 'clip-' + k.replace(':', '')}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.master, d / "01-x.mp4")
        for p in ("youtube", "tiktok", "facebook", "reels"):
            (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")

    def ready(self, *slots: str) -> None:
        for k in slots:
            self.deliver(k)
        K.collect(self.cfg, self.gta, DAY)

    def slot(self, k: str = "07:00") -> dict:
        return B.read(self.gta, DAY)["slots"][k]

    def with_comment(self, k: str = "07:00", poll=None, trial=None) -> dict:
        with EX.edit(self.cfg, self.gta, DAY, k, self.who, "first comment") as x:
            x["first_comment"] = {"facebook_reels": {"text": FC, "from": "edited"}}
            if poll:
                x["poll"] = {"instagram_reels": {"options": poll, "from": "task"}}
            if trial:
                x["trial"] = {"instagram_reels": trial}
        return self.slot(k)

    def mode(self, **modes) -> None:
        p = C.STATE / "gaming" / "social" / "settings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"modes": {k.replace("__", "."): v for k, v in modes.items()}}))

    # ---------------------------------------------------------------------------------------------- the signature
    def test_1_a_clip_without_extras_keeps_its_old_signature(self):
        self.ready("07:00")
        s = self.slot()
        self.assertNotIn("extras", s)
        self.assertEqual(B.content_hash(s), old_content_hash(s))
        cut = dict(s, cuts={"instagram_reels": {"path": "x", "sha256": "c" * 64}})
        self.assertEqual(B.content_hash(cut), old_content_hash(cut))
        for empty in ({}, {"path": ""}, None):                  # a pointer with nothing in it signs nothing new
            self.assertEqual(B.content_hash(dict(s, extras=empty)), old_content_hash(s))
        self.assertIsNone(EX.hash_parts(s))
        self.assertIsNone(EX.seen_parts(s))
        self.assertEqual(EX.problems(s), [])

    def test_2_extras_change_the_hash_and_the_seen_key(self):
        self.ready("07:00")
        before = self.slot()
        s = self.with_comment()
        x = s["extras"]
        p = Path(x["path"])
        self.assertEqual(p, C.STATE / "gaming" / "extras" / DAY.isoformat() / "0700.json")
        self.assertEqual(x["sha256"], hashlib.sha256(p.read_bytes()).hexdigest())
        self.assertEqual(EX.hash_parts(s), x["sha256"])
        self.assertNotEqual(B.content_hash(s), old_content_hash(s))
        self.assertNotEqual(B.seen_key(s), B.seen_key(before))
        data = json.loads(p.read_text())
        self.assertEqual(p.read_text(), json.dumps(data, indent=1, sort_keys=True) + "\n")
        self.assertEqual((data["v"], data["account"], data["day"], data["slot"], data["clip_sha"]),
                         (1, "gaming", DAY.isoformat(), "07:00", s["clip"]["sha256"]))
        self.assertEqual(data["first_comment"], {"facebook_reels": {"text": FC, "from": "edited"}})
        self.assertEqual([(e["by"], e["what"]) for e in data["edited"]], [(self.who, "first comment")])
        self.assertEqual(EX.first_comment(s, "facebook_reels"), FC)
        self.assertEqual(EX.first_comment(s, "instagram_reels"), "")
        key = B.seen_key(s)
        with EX.edit(self.cfg, self.gta, DAY, "07:00", self.who, "edited") as d:
            d["first_comment"]["facebook_reels"]["text"] = FC + " (the full hearing)"
        s2 = self.slot()
        self.assertNotEqual(B.seen_key(s2), key)                # the page redraws the card it showed
        self.assertNotEqual(B.content_hash(s2), B.content_hash(s))
        self.assertEqual(len(json.loads(p.read_text())["edited"]), 2)
        with EX.edit(self.cfg, self.gta, DAY, "07:00", self.who, "emptied") as d:
            d["first_comment"]["facebook_reels"]["text"] = "  "    # an empty box: no first comment, no file
        self.assertNotIn("extras", self.slot())
        self.assertFalse(p.exists())

    def test_3_verify_catches_an_edit_and_a_file_gone(self):
        self.ready("07:00")
        self.with_comment()
        S.approve(self.cfg, self.gta, DAY, ["07:00"], self.who)
        s = self.slot()
        self.assertIn(s["status"], B.SIGNED)
        self.assertEqual(B.verify(s), [])
        p = Path(s["extras"]["path"])
        p.write_text(p.read_text().replace("Creator", "Someone else"))
        want = "the first comment or poll was edited after it was collected (Pull it, then approve it again)"
        self.assertIn(want, B.verify(s))
        self.assertIn(want, B.verify(s, cached=True))
        self.assertEqual(EX.problems(s), [want])
        self.assertIn("what is on the board is not what was signed", B.verify(dict(s, extras=None)))
        p.unlink()
        self.assertIn(f"the first comment or poll file is gone: {p}", B.verify(s))
        self.assertEqual(EX.problems(s), [f"the first comment or poll file is gone: {p}"])
        self.assertEqual(EX.seen_parts(s), [str(p), None])
        self.assertEqual(SOC.extras_health(self.cfg, self.cfg.slot_time(DAY, "06:00"), [self.gta])[0], "bad")

    def test_4_approve_takes_the_file_as_it_is_and_drops_one_gone(self):
        self.ready("07:00", "08:45")
        s = self.with_comment("07:00")
        p = Path(s["extras"]["path"])
        p.write_text(p.read_text().replace("Creator", "the Creator"))     # by hand, before signing
        lines = S.approve(self.cfg, self.gta, DAY, ["07:00"], self.who)
        self.assertIn("approved by", "\n".join(lines))
        s = self.slot("07:00")
        self.assertEqual(s["extras"]["sha256"], hashlib.sha256(p.read_bytes()).hexdigest())
        self.assertEqual(s["signoff"]["hash"], B.content_hash(s))
        self.assertEqual(B.verify(s), [])
        self.assertTrue(any("first comment or poll edited after collecting; signed as it is now" in n
                            for n in s["notes"]))
        s = self.with_comment("08:45")
        Path(s["extras"]["path"]).unlink()
        S.approve(self.cfg, self.gta, DAY, ["08:45"], self.who)
        s = self.slot("08:45")
        self.assertIn(s["status"], B.SIGNED)
        self.assertNotIn("extras", s)
        self.assertEqual(B.verify(s), [])
        self.assertTrue(any("signed without it" in n for n in s["notes"]))

    def test_5_edit_refuses_a_signed_clip_and_bad_words(self):
        self.ready("07:00")
        with self.assertRaises(SystemExit) as e:
            with EX.edit(self.cfg, self.gta, DAY, "07:00", self.who, "poll") as d:
                d["poll"] = {"instagram_reels": {"options": ["Only one"]}}
        self.assertEqual(str(e.exception), "poll: two to four options")
        for bad in ({"first_comment": {"tiktok": {"text": "x"}}}, {"poll": {"facebook_reels": {"options": ["a", "b"]}}},
                    {"poll": {"instagram_reels": {"options": ["a", "b" * 26]}}},
                    {"trial": {"instagram_reels": {"graduation_strategy": "NOW"}}}, {"stickers": {}}):
            with self.assertRaises(SystemExit):
                EX.check(bad)
        self.assertNotIn("extras", self.slot())
        self.with_comment()
        S.approve(self.cfg, self.gta, DAY, ["07:00"], self.who)
        with self.assertRaises(SystemExit) as e:
            with EX.edit(self.cfg, self.gta, DAY, "07:00", self.who, "first comment") as d:
                d["first_comment"]["facebook_reels"]["text"] = "after signing"
        self.assertEqual(str(e.exception), "It is signed: Pull it to change its first comment.")
        self.assertEqual(B.verify(self.slot()), [])
        S.pull(self.cfg, self.gta, DAY, ["07:00"], self.who)
        with EX.edit(self.cfg, self.gta, DAY, "07:00", self.who, "first comment") as d:
            d["first_comment"]["facebook_reels"]["text"] = "after pulling"
        self.assertEqual(EX.first_comment(self.slot(), "facebook_reels"), "after pulling")

    def test_6_write_locked_needs_the_board_held_and_a_ready_clip(self):
        self.ready("07:00")
        s = self.slot()
        with self.assertRaises(SystemExit):
            EX.write_locked(self.gta, DAY, s, {"first_comment": {"facebook_reels": {"text": FC}}}, "t", "x")
        with B.edit(self.cfg, self.gta, DAY) as bd:
            EX.write_locked(self.gta, DAY, bd["slots"]["07:00"], {"first_comment": {"facebook_reels": {"text": FC}}},
                            "collect", "defaults")
            with self.assertRaises(SystemExit):
                EX.write_locked(self.gta, DAY, bd["slots"]["08:45"], {"first_comment": {}}, "collect", "defaults")
        self.assertEqual(EX.first_comment(self.slot(), "facebook_reels"), FC)

    # ---------------------------------------------------------------------------------------------- clearing
    def test_7_a_new_clip_and_make_again_clear_the_extras(self):
        self.ready("07:00")
        p = Path(self.with_comment()["extras"]["path"])
        S.decline(self.cfg, self.gta, DAY, ["07:00"], self.who, remake=True)
        self.assertNotIn("extras", self.slot())
        self.assertFalse(p.exists())
        # a pointer left on an open slot (an older board) goes when collect takes a new clip into it
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
        outside = self.tmp / "keep.json"
        outside.write_text("{}")
        with B.edit(self.cfg, self.gta, DAY) as bd:
            bd["slots"]["07:00"]["extras"] = {"path": str(p), "sha256": "0" * 64}
            bd["slots"]["08:45"]["extras"] = {"path": str(outside), "sha256": "0" * 64}
        self.deliver("07:00", "again-0700")
        self.deliver("08:45", "again-0845")
        K.collect(self.cfg, self.gta, DAY)
        self.assertEqual(self.slot()["status"], B.READY)
        self.assertNotIn("extras", self.slot())
        self.assertNotIn("extras", self.slot("08:45"))
        self.assertFalse(p.exists())
        self.assertTrue(outside.exists())                        # never a file outside state/<slug>/extras

    def test_8_after_take_writes_nothing_while_every_part_is_off(self):
        self.ready("07:00", "08:45")
        for k in ("07:00", "08:45"):
            self.assertNotIn("extras", self.slot(k))
        self.assertFalse((C.STATE / "gaming" / "extras").exists())

    def test_9_a_part_adds_extras_at_collect_and_they_are_signed(self):
        mod = types.ModuleType("clipfactory.sxfc")
        seen = []

        def on_collect(ctx):
            seen.append((ctx.acct.slug, ctx.day, ctx.slot["slot"], ctx.slot["status"]))
            ctx.extras["first_comment"] = {"facebook_reels": {"text": FC, "from": "default"}}
        mod.on_collect = on_collect
        self.inject("sxfc", mod)
        with mock.patch.object(REG, "AFTER_TAKE", ("sxfc:on_collect",)):
            self.ready("07:00")
        self.assertEqual(seen, [("gaming", DAY, "07:00", B.READY)])
        s = self.slot()
        self.assertEqual(json.loads(Path(s["extras"]["path"]).read_text())["edited"][0]["by"], "collect")
        self.assertEqual(EX.first_comment(s, "facebook_reels"), FC)
        S.approve(self.cfg, self.gta, DAY, ["07:00"], self.who)
        s = self.slot()
        self.assertEqual(s["signoff"]["hash"], B.content_hash(s))
        self.assertEqual(B.verify(s), [])

        def boom(ctx):
            raise SystemExit("a part that refuses")
        mod.on_collect = boom
        with mock.patch.object(REG, "AFTER_TAKE", ("sxfc:on_collect",)):
            self.ready("08:45")                                 # a part that refuses never loses the clip
        self.assertEqual(self.slot("08:45")["status"], B.READY)

    # ---------------------------------------------------------------------------------------------- the reel
    def test_10_ig_form_only_when_switched_on_and_signed(self):
        self.ready("07:00")
        trial = {"graduation_strategy": "SS_PERFORMANCE", "experiment": "e-0003", "arm": "B"}
        self.with_comment(poll=["Yes", "No"], trial=trial)
        self.mode(poll="on", trial_reels="on")
        self.assertEqual(EX.ig_form(self.slot(), self.gta), {})         # not signed yet
        S.approve(self.cfg, self.gta, DAY, ["07:00"], self.who)
        s = self.slot()
        self.assertEqual(EX.ig_form(s, self.gta),
                         {"poll_attachment": '{"option_a":"Yes","option_b":"No"}',
                          "trial_params": '{"graduation_strategy":"SS_PERFORMANCE"}'})
        self.assertEqual(EX.ig_form(s, "gaming")["poll_attachment"], '{"option_a":"Yes","option_b":"No"}')
        self.mode(poll="practice", trial_reels="on")
        self.assertEqual(EX.ig_form(s, self.gta), {"trial_params": '{"graduation_strategy":"SS_PERFORMANCE"}'})
        self.mode(poll="on")
        self.assertEqual(EX.ig_form(s, self.gta), {"poll_attachment": '{"option_a":"Yes","option_b":"No"}'})
        self.assertEqual(EX.ig_form(s, self.cfg.account("politics")), {})     # another account's switches
        self.assertEqual(EX.ig_form(dict(s, signoff={"hash": "0" * 64}), self.gta), {})
        p = Path(s["extras"]["path"])
        p.write_text(p.read_text().replace('"Yes"', '"Maybe"'))
        self.assertEqual(EX.ig_form(s, self.gta), {})                   # changed after signing
        p.unlink()
        self.assertEqual(EX.ig_form(s, self.gta), {})
        self.mode()
        self.assertEqual(EX.ig_form(s, self.gta), {})

    def test_11_a_trial_needs_an_experiment_arm(self):
        self.ready("07:00")
        self.with_comment(trial={"graduation_strategy": "MANUAL"})
        S.approve(self.cfg, self.gta, DAY, ["07:00"], self.who)
        self.mode(trial_reels="on")
        self.assertEqual(EX.ig_form(self.slot(), self.gta), {})

    def test_12_ig_post_sends_the_signed_poll_and_nothing_when_off(self):
        self.ready("07:00")
        self.with_comment(poll=["Yes", "No"])
        S.approve(self.cfg, self.gta, DAY, ["07:00"], self.who)
        s = self.slot()
        forms = []

        def http(method, url, params=None, form=None, **kw):
            if form is not None and url.endswith("/media"):
                forms.append(dict(form))
                raise D.PlatformError("stop here")
            raise AssertionError(url)
        with mock.patch.object(D, "_load", lambda acct, *a: {"page_token": "t", "ig_user_id": "17"}), \
                mock.patch.object(D, "in_band", lambda *a, **k: None), mock.patch.object(D, "_http", http):
            for modes in ({}, {"poll": "on"}):
                self.mode(**modes)
                with self.assertRaises(D.PlatformError):
                    D.ig_post(self.gta, Path(s["clip"]["path"]), {"caption": "c"}, dict(s))
        self.assertNotIn("poll_attachment", forms[0])
        self.assertEqual(forms[1]["poll_attachment"], '{"option_a":"Yes","option_b":"No"}')
        self.assertEqual(forms[1]["caption"], "c")


if __name__ == "__main__":
    unittest.main()
