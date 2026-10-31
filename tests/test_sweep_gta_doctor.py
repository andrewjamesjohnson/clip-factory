"""sweep-gta (2026-10-17 audit): doctor names a dirty GTA skill tree (GTA-6) and a board whose drafts were dragged away
(FOLD-2). (The 03:45 retry is test_sweep_gta_retry.py.) Offline; a throwaway git repo stands in for the skill, a temp
folder for the Desktop."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-sweepgta-doctor-state-")   # before the import

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import factory as F                      # noqa: E402
from clipfactory import board as B       # noqa: E402
from clipfactory import config as C      # noqa: E402


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True, text=True)


class SkillTree(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="clipfactory-sweepgta-skill-"))
        git(self.repo, "init", "-q", "-b", "audit/three-lanes")
        for rel in ("scripts/run.py", "SKILL.md", "references/jobs/INDEX.md", "broll-library/index.json"):
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "one")
        self.acct = SimpleNamespace(name="Gaming", skill_dir=self.repo, maker="gta")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_clean_names_the_branch_and_commit(self):
        good, words = F.skill_tree(self.acct)
        self.assertTrue(good, words)
        self.assertIn("clean at audit/three-lanes", words)

    def test_a_delivery_archive_or_the_library_is_not_the_tool(self):
        (self.repo / "references" / "jobs" / "INDEX.md").write_text("y\n")
        (self.repo / "references" / "jobs" / "new-job").mkdir()
        (self.repo / "references" / "jobs" / "new-job" / "a.json").write_text("{}")
        (self.repo / "broll-library" / "index.json").write_text("y\n")
        self.assertTrue(F.skill_tree(self.acct)[0])

    def test_an_uncommitted_script_or_skill_md_is_named(self):
        (self.repo / "scripts" / "run.py").write_text("changed\n")
        (self.repo / "SKILL.md").write_text("changed\n")
        good, words = F.skill_tree(self.acct)
        self.assertFalse(good)
        self.assertIn("2 uncommitted file(s)", words)
        self.assertIn("scripts/run.py", words)

    def test_git_is_read_without_taking_the_index_lock(self):
        real = subprocess.run
        seen = []

        def spy(cmd, *a, **kw):
            seen.append((kw.get("env") or {}).get("GIT_OPTIONAL_LOCKS"))
            return real(cmd, *a, **kw)
        with mock.patch.object(F.subprocess, "run", side_effect=spy):
            self.assertTrue(F.skill_tree(self.acct)[0])
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"0"})

    def test_no_repo_is_not_clean(self):
        good, words = F.skill_tree(SimpleNamespace(name="Gaming", skill_dir=self.repo / "nope", maker="gta"))
        self.assertFalse(good, words)


class BoardPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-sweepgta-paths-"))
        self._state = C.STATE
        C.STATE = self.tmp / "state"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "Desktop" / "Clip Drafts")
        (self.tmp / "accounts.json").write_text(json.dumps(raw))
        self.cfg = C.load(self.tmp / "accounts.json")
        self.acct = self.cfg.account("gaming")
        self.day = self.cfg.now().date()
        clip = self.cfg.drafts_root / "Gaming" / "0700 x" / "01-x.mp4"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"x")
        with B.edit(self.cfg, self.acct, self.day) as bd:
            bd["slots"]["07:00"].update(status=B.READY, folder=str(clip.parent), clip={"path": str(clip)},
                                         copy={"tiktok": {"path": str(clip.parent / "tiktok.txt")}})
        (clip.parent / "tiktok.txt").write_text("w")
        self.clip = clip

    def tearDown(self):
        C.STATE = self._state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_all_there(self):
        with mock.patch.object(Path, "home", return_value=self.tmp):
            self.assertEqual(F.board_paths(self.cfg), ([], []))

    def test_a_dragged_folder_and_its_empty_twin_are_named(self):
        moved = self.tmp / "Desktop" / "Gaming Run 10.17.2026" / "Clip Drafts"
        moved.parent.mkdir(parents=True)
        shutil.move(str(self.cfg.drafts_root), str(moved))       # the Finder drag of 10-17 15:30
        self.cfg.drafts_root.mkdir(parents=True)                 # ... and the factory's new empty one
        with mock.patch.object(Path, "home", return_value=self.tmp):
            gone, extra = F.board_paths(self.cfg)
        self.assertIn(str(self.clip), gone)
        self.assertEqual(extra, [moved])


if __name__ == "__main__":
    unittest.main()
