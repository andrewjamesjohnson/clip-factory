import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Moving an account to the Mac Mini (2026-10-20): factory.py social export copies state/<account>/ and its stats
# series into an empty folder with a manifest of sha256s; social import checks every file first, refuses while the
# account runs here or already has state (unless --replace, which moves the old state aside, never deletes it).
# Throwaway folders only; no network.

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from social_fakes import cfg_in_tmp  # noqa: E402

from clipfactory import config as C         # noqa: E402
from clipfactory import social as SOC       # noqa: E402
from clipfactory import socialmove as MV    # noqa: E402


def said(fn, *a) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a)
    return buf.getvalue()


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_in_tmp(self)
        self.old_state = C.STATE
        s = C.STATE
        self.files = {
            "gaming/2031-07-08.json": '{"slots": {}}',
            "gaming/outward/actions-2031-07.jsonl": '{"t": "x"}\n',
            "gaming/social/settings.json": '{"v": 1, "modes": {"guard": "watch"}}',
            "gaming/engage/items-2031-07.json": '{"v": 1, "items": {}}',
            "gaming/cards/2031-07-08/quote-1730/1.jpg": "\xff\xd8fake",
            "stats/series/gaming__facebook_reels__2031-07.json": "{}",
            "stats/pulled/gaming__youtube_shorts.json": "{}",
        }
        for rel, text in self.files.items():
            p = s / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        for rel in ("gaming/2031-07-08.json.lock", "gaming/outward/.lock", "gaming/x.json.tmp",
                    "politics/2031-07-08.json", "stats/series/politics__facebook_reels__2031-07.json",
                    "stats/posts.json"):
            p = s / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("not moved")
        self.out = Path(tempfile.mkdtemp(prefix="move-")) / "gta"
        raw = json.loads(self.cfg_path.read_text())                   # step 1 of the move: switched off here
        raw["accounts"]["gaming"]["active"] = False
        off = self.tmp / "main-off-accounts.json"
        off.write_text(json.dumps(raw))
        self.off = C.load(off)

    def other_machine(self, gta_active: bool = False) -> C.Config:
        """A fresh state (the Mac Mini) with gaming not switched on there yet."""
        raw = json.loads(self.cfg_path.read_text())
        raw["accounts"]["gaming"]["active"] = gta_active
        p = self.tmp / "mini-accounts.json"
        p.write_text(json.dumps(raw))
        C.STATE = Path(tempfile.mkdtemp(prefix="mini-state-"))
        return C.load(p)

    def test_1_round_trip_with_a_manifest(self):
        out = said(SOC.cli, self.off, ["export", "gaming", "--to", str(self.out)])
        self.assertIn("Exported Gaming: 7 files", out)
        hint = out.split("on the other Mac: ", 1)[1].split(" (add", 1)[0]
        self.assertEqual(hint, "python3 factory.py social import <that folder>")
        m = json.loads((self.out / "manifest.json").read_text())
        self.assertEqual((m["v"], m["account"], m["machine"]), (1, "gaming", "main"))
        self.assertEqual(sorted(m["files"]), sorted(self.files))
        for rel, sha in m["files"].items():
            self.assertEqual(sha, hashlib.sha256((self.out / rel).read_bytes()).hexdigest())
        self.assertFalse((self.out / "gaming" / "outward" / ".lock").exists())
        self.assertFalse((self.out / "politics").exists())
        mini = self.other_machine()
        out = said(SOC.cli, mini, ["import", "--from", str(self.out)])     # social.cli hands --from on whole
        self.assertIn("Imported Gaming: 7 files", out)
        self.assertIn("every sha256 checked", out)
        for rel, text in self.files.items():
            self.assertEqual((C.STATE / rel).read_text(), text, rel)
        self.assertFalse(list(C.STATE.glob(".import-*")))
        with self.assertRaises(SystemExit) as ex:                          # the hint's own form parses too
            said(SOC.cli, mini, hint.replace("python3 factory.py social ", "").replace("<that folder>",
                                                                               str(self.out)).split())
        self.assertIn("add --replace", str(ex.exception))

    def test_2_export_refusals(self):
        with self.assertRaises(SystemExit) as ex:
            MV.cli_export(self.off, ["gaming", "--to", "relative/folder"])
        self.assertIn("a full path", str(ex.exception))
        self.out.mkdir(parents=True)
        (self.out / "something").write_text("x")
        with self.assertRaises(SystemExit) as ex:
            MV.cli_export(self.off, ["gaming", "--to", str(self.out)])
        self.assertIn("is not empty", str(ex.exception))
        with self.assertRaises(SystemExit) as ex:
            MV.cli_export(self.off, ["gaming", "--to", str(C.STATE / "export-here")])
        self.assertIn("inside the state folder", str(ex.exception))
        with self.assertRaises(SystemExit):
            MV.cli_export(self.off, ["nobody", "--to", str(self.out.parent / "new")])

    def test_2b_export_refused_while_it_runs_here(self):
        # main still runs it: the queue and the politics would keep moving after the copy, and the Mini would send a
        # queued first comment that main had already sent
        with self.assertRaises(SystemExit) as ex:
            MV.cli_export(self.cfg, ["gaming", "--to", str(self.out)])
        self.assertIn("Gaming is switched on here", str(ex.exception))
        self.assertIn('"active": false', str(ex.exception))
        self.assertFalse(self.out.exists())

    def test_3_import_refusals(self):
        said(MV.cli_export, self.off, ["gaming", "--to", str(self.out)])
        with self.assertRaises(SystemExit) as ex:                             # it runs here
            MV.cli_import(self.cfg, ["--from", str(self.out)])
        self.assertIn("is switched on here", str(ex.exception))
        mini = self.other_machine()
        (self.out / "gaming" / "social" / "settings.json").write_text('{"v": 1, "modes": {"guard": "on"}}')
        with self.assertRaises(SystemExit) as ex:                             # a file changed after the export
            MV.cli_import(mini, ["--from", str(self.out)])
        self.assertIn("differ from the manifest, nothing was imported", str(ex.exception))
        self.assertFalse((C.STATE / "gaming").exists())
        (self.out / "gaming" / "social" / "settings.json").write_text(self.files["gaming/social/settings.json"])
        m = json.loads((self.out / "manifest.json").read_text())
        m["files"]["../../etc/x"] = "0" * 64
        (self.out / "manifest.json").write_text(json.dumps(m))
        with self.assertRaises(SystemExit) as ex:
            MV.cli_import(mini, ["--from", str(self.out)])
        self.assertIn("not a path inside the move", str(ex.exception))
        del m["files"]["../../etc/x"]
        (self.out / "manifest.json").write_text(json.dumps(m))
        with self.assertRaises(SystemExit) as ex:
            MV.cli_import(mini, ["--from", str(self.out / "nope")])
        self.assertIn("no such folder", str(ex.exception))
        with self.assertRaises(SystemExit) as ex:
            MV.cli_import(mini, [])
        self.assertIn("a full path", str(ex.exception))
        with self.assertRaises(SystemExit) as ex:
            MV.cli_import(mini, [str(self.out), "--from", "/elsewhere"])
        self.assertIn("one folder", str(ex.exception))
        self.assertIn("Imported", said(MV.cli_import, mini, ["--", "--from", str(self.out)]))

    def test_4_replace_moves_the_old_state_aside(self):
        said(MV.cli_export, self.off, ["gaming", "--to", str(self.out)])
        mini = self.other_machine()
        old = C.STATE / "gaming" / "2031-01-01.json"
        old.parent.mkdir(parents=True)
        old.write_text("the old board")
        (C.STATE / "stats" / "series").mkdir(parents=True)
        (C.STATE / "stats" / "series" / "gaming__old.json").write_text("old series")
        with self.assertRaises(SystemExit) as ex:
            MV.cli_import(mini, ["--from", str(self.out)])
        self.assertIn("add --replace", str(ex.exception))
        out = said(MV.cli_import, mini, ["--from", str(self.out), "--replace"])
        self.assertIn("the state that was here is in", out)
        backups = list((C.STATE / "config-backups").glob("move-gaming-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "gaming" / "2031-01-01.json").read_text(), "the old board")
        self.assertEqual((backups[0] / "stats" / "series" / "gaming__old.json").read_text(), "old series")
        self.assertFalse(old.exists())
        self.assertEqual((C.STATE / "gaming" / "2031-07-08.json").read_text(), '{"slots": {}}')

    def replace_setup(self):
        said(MV.cli_export, self.off, ["gaming", "--to", str(self.out)])
        mini = self.other_machine()
        old = C.STATE / "gaming" / "2031-01-01.json"
        old.parent.mkdir(parents=True)
        old.write_text("the old board")
        (C.STATE / "stats" / "series").mkdir(parents=True)
        (C.STATE / "stats" / "series" / "gaming__old.json").write_text("old series")
        return mini, old

    def test_4b_a_copy_that_fails_leaves_the_old_state_in_place(self):
        mini, old = self.replace_setup()
        real, n = MV.shutil.copy2, [0]

        def full(a, b, *k, **kw):                                         # the disk fills on the second file
            n[0] += 1
            if n[0] == 2:
                raise OSError(28, "No space left on device")
            return real(a, b, *k, **kw)
        with mock.patch.object(MV.shutil, "copy2", full):
            with self.assertRaises(OSError):
                MV.cli_import(mini, [str(self.out), "--replace"])
        self.assertEqual(old.read_text(), "the old board")
        self.assertEqual((C.STATE / "stats" / "series" / "gaming__old.json").read_text(), "old series")
        self.assertFalse((C.STATE / "config-backups").exists())
        self.assertFalse(list(C.STATE.glob(".import-*")))

        def torn(a, b, *k, **kw):                                         # a file changes while it is copied
            out = real(a, b, *k, **kw)
            if str(a).endswith("settings.json"):
                Path(b).write_text("torn")
            return out
        with mock.patch.object(MV.shutil, "copy2", torn):
            with self.assertRaises(SystemExit) as ex:
                MV.cli_import(mini, [str(self.out), "--replace"])
        self.assertIn("the state here is as it was", str(ex.exception))
        self.assertEqual(old.read_text(), "the old board")
        self.assertFalse((C.STATE / "config-backups").exists())

    def test_4c_a_swap_that_fails_puts_the_old_state_back(self):
        mini, old = self.replace_setup()
        real = MV.os.replace

        def stuck(a, b):
            if "stats" in str(b):
                raise OSError(5, "Input/output error")
            return real(a, b)
        with mock.patch.object(MV.os, "replace", stuck):
            with self.assertRaises(SystemExit) as ex:
                MV.cli_import(mini, [str(self.out), "--replace"])
        self.assertIn("nothing was imported, and the state here is as it was", str(ex.exception))
        self.assertEqual(old.read_text(), "the old board")
        self.assertFalse((C.STATE / "gaming" / "2031-07-08.json").exists())
        self.assertEqual((C.STATE / "stats" / "series" / "gaming__old.json").read_text(), "old series")
        self.assertFalse((C.STATE / "stats" / "pulled" / "gaming__youtube_shorts.json").exists())

    def test_5_a_client_never_moves_this_way(self):
        raw = json.loads(self.cfg_path.read_text())
        raw["accounts"]["client-co"] = dict(raw["accounts"]["gaming"], name="Client Co", owner="client",
                                            approvers=["Bob"])
        self.cfg_path.write_text(json.dumps(raw))
        cfg = C.load(self.cfg_path)
        with self.assertRaises(SystemExit) as ex:
            MV.cli_export(cfg, ["client-co", "--to", str(self.out)])
        self.assertIn("client's account", str(ex.exception))


if __name__ == "__main__":
    unittest.main()
