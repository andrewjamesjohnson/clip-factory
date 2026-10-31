"""No test run reads or writes the live state folder (~/clip-factory/state), whatever order the test files load in
(clipfactory/livestate.py). On 2026-10-17 a test file that sorted first imported clipfactory before CLIP_FACTORY_STATE
was pinned, and the run wrote 2030 boards and test stats into the live folder.

Nothing here can write to the real one: the checks on it only list and read, and every write is tried on a throwaway
copy of the factory, whose own state/ plays the live folder."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-guard-state-")

import ast
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C       # noqa: E402
from clipfactory import livestate as L    # noqa: E402
from clipfactory import makers as M       # noqa: E402
from clipfactory import stats as ST       # noqa: E402

LIVE = ROOT / "state"

# the 2026-10-17 file: sorts first, pins nothing (no CLIP_FACTORY_OFFLINE, no CLIP_FACTORY_STATE), imports clipfactory
FIRST = '''
import os, shutil, subprocess, sys, unittest
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import board as B, config as C, livestate as L, makers as M, stats as ST

LIVE = C.ROOT / "state"


class First(unittest.TestCase):
    def test_1_its_writes_land_in_a_throwaway_folder(self):
        cfg = C.load()
        for p in (C.STATE, ST.OUT, M.render_lock(cfg.account("gaming")), os.environ["CLIP_FACTORY_STATE"]):
            self.assertFalse(L.is_live(p), p)
        with B.edit(cfg, cfg.account("gaming"), date(2030, 1, 2)) as bd:      # what test_factory did that day
            bd["report"] = "a test board"
        (ST.OUT / "pulled").mkdir(parents=True, exist_ok=True)
        (ST.OUT / "pulled" / "gaming__youtube_shorts.json").write_text("{}")
        self.assertTrue((C.STATE / "gaming" / "2030-01-02.json").is_file())

    def test_2_the_live_folder_by_hand_is_refused(self):
        tries = [lambda: (LIVE / "gaming" / "2030-01-03.json").write_text("{}"),
                 lambda: (LIVE / "post-due.lock").touch(),
                 lambda: os.listdir(LIVE),
                 lambda: (LIVE / "sentinel.txt").read_text(),
                 lambda: os.replace(LIVE / "sentinel.txt", C.STATE / "moved.txt"),
                 lambda: shutil.copytree(LIVE, C.STATE / "copy"),
                 lambda: shutil.rmtree(LIVE / "gaming", ignore_errors=True),
                 lambda: (LIVE / "stats").mkdir(),
                 lambda: subprocess.run(["touch", str(LIVE / "x")]),
                 lambda: subprocess.run("rm -f " + str(LIVE / "sentinel.txt"), shell=True),
                 lambda: subprocess.run(["true"], cwd=str(LIVE)),
                 lambda: os.chdir(LIVE)]
        for i, attempt in enumerate(tries):
            with self.assertRaises(L.LiveState, msg=i):
                attempt()
'''
# a well-behaved file, as test_factory is: offline and its own folder before it imports clipfactory
LAST = '''
import os, tempfile
os.environ["CLIP_FACTORY_OFFLINE"] = "1"
os.environ["CLIP_FACTORY_STATE"] = tempfile.mkdtemp(prefix="clipfactory-last-")
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipfactory import config as C, livestate as L


class Last(unittest.TestCase):
    def test_its_writes_land_in_a_throwaway_folder(self):
        self.assertFalse(L.is_live(C.STATE))
        (C.STATE / "politics").mkdir(parents=True, exist_ok=True)
        (C.STATE / "politics" / "2030-02-01.json").write_text("{}")
'''
# the suite loaded backwards
REVERSED = '''
import os, sys, unittest
sys.path.insert(0, "tests")
names = sorted((f[:-3] for f in os.listdir("tests") if f.startswith("test") and f.endswith(".py")), reverse=True)
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in names)
sys.exit(not unittest.TextTestRunner().run(suite).wasSuccessful())
'''
# the README's plain `python3 -m unittest discover -s tests`: no CLIP_FACTORY_OFFLINE anywhere in the environment
PLAIN = '''
import os, runpy, sys
os.environ.pop("CLIP_FACTORY_OFFLINE", None)
sys.argv = ["python -m unittest", "discover", "-s", "tests"]
runpy.run_module("unittest", run_name="__main__", alter_sys=True)
'''
# a real run (launchd, the review page): neither signal, so STATE is the folder's own state/ as before and no hook
REAL = '''
import os, sys
os.environ.pop("CLIP_FACTORY_OFFLINE", None)
sys.path.insert(0, ".")
from clipfactory import config as C, livestate as L
assert C.STATE == C.ROOT / "state", C.STATE
assert not L._installed
(C.STATE / "real-run.txt").write_text("a real run writes here")
print("real run ok")
'''


def listing(folder: Path) -> list:
    out = []
    for d, dirs, files in os.walk(folder):
        dirs.sort()
        for n in sorted(files):
            p = Path(d) / n
            st = p.stat()
            out.append((str(p.relative_to(folder)), st.st_size, st.st_mtime_ns, hashlib.sha1(p.read_bytes()).hexdigest()))
    return out


class ThisRunTest(unittest.TestCase):
    """The run these tests are in: whichever file came first, nothing here points at, opens or lists the live folder.
    Only reads are tried on it, so even a broken guard could not change it."""

    def test_1_nothing_in_this_run_points_at_the_live_folder(self):
        self.assertTrue(L.test_run())
        lock = M.render_lock(C.load().account("gaming"))
        for p in (C.STATE, ST.OUT, lock, os.environ["CLIP_FACTORY_STATE"]):
            self.assertFalse(L.is_live(p), p)

    def test_2_every_name_for_the_live_folder_counts(self):
        installed = Path(os.path.expanduser("~/clip-factory/state"))     # from any copy of the code, too
        for p in (LIVE, LIVE / "gaming" / "x.json", os.path.relpath(LIVE / "stats"), installed / "politics",
                  str(LIVE / "logs" / ".." / "stats")):
            self.assertTrue(L.is_live(p), p)
        if LIVE.exists():                                    # the same folder under macOS's other name for it
            self.assertTrue(L.is_live("/System/Volumes/Data" + os.path.realpath(LIVE)))
        tmp = Path(tempfile.mkdtemp(prefix="clipfactory-guard-names-"))
        link = tmp / "link"
        link.symlink_to(tmp / "real")
        old = L.LIVE, L._TOPS
        L.LIVE, L._TOPS = L.LIVE + (tmp / "real" / "state",), None       # one more live folder, not made yet
        try:
            for p in (tmp / "real" / "state", str(tmp / "real" / "STATE" / "x.json").upper(), link / "state" / "y"):
                self.assertTrue(L.is_live(p), p)
            self.assertFalse(L.is_live(tmp / "real" / "state-old"))
        finally:
            L.LIVE, L._TOPS = old

    def test_3_reading_the_live_folder_is_refused(self):
        for i, attempt in enumerate([lambda: os.listdir(LIVE), lambda: list(LIVE.glob("*.json")),
                                     lambda: open(LIVE / "no-such-board.json"), lambda: os.listdir(str(LIVE).upper()),
                                     lambda: os.listdir(os.path.relpath(LIVE)), lambda: list(os.walk(LIVE))]):
            with self.assertRaises(L.LiveState, msg=i):
                attempt()

    def test_4_no_child_process_reaches_it(self):
        for i, attempt in enumerate([
                lambda: subprocess.run(["true"], cwd=LIVE),
                lambda: subprocess.run(["ls", str(LIVE)], capture_output=True),
                lambda: subprocess.run(f"ls {LIVE}/ >/dev/null", shell=True),
                lambda: subprocess.run(["true"], env=dict(os.environ, CLIP_FACTORY_STATE=str(LIVE))),
                lambda: subprocess.run(["true"], env={"PATH": "/usr/bin:/bin"}),     # not offline: refused too
                lambda: os.system(f"ls {LIVE} >/dev/null")]):
            with self.assertRaises(L.LiveState, msg=i):
                attempt()


class WalkerTest(unittest.TestCase):
    """shutil.rmtree and os.fwalk open each entry by name at a folder fd, which the open event leaves out
    (livestate._walker_fd). On 2026-10-20 Anaconda's 3.13 read the wrong local: a temp folder's child "state" was taken
    from the working folder (~/clip-factory/state) and test_politics_fixes' tearDownClass stopped the run. Every live
    folder here is a throwaway one, so even a broken guard could not touch the real one."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-guard-walk-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(os.chdir, os.getcwd())

    def tree(self, top: Path) -> Path:
        """A tree with a "state" child at two depths, as test_politics_fixes' temp folder has."""
        for d in ("state/gaming", "a/state", "b"):
            (top / d).mkdir(parents=True)
        for f in ("state/gaming/2030-01-02.json", "state/post-due.lock", "a/state/x.json", "b/y.txt"):
            (top / f).write_text("{}")
        return top

    def fd(self, folder: Path) -> int:
        fd = os.open(folder, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        return fd

    def test_1_a_temp_state_child_is_not_the_live_one(self):
        """From a folder whose own state/ is live (this tree's, and the installed factory's), as the README runs it."""
        installed = Path(L._home()) / "clip-factory"
        for i, cwd in enumerate([ROOT] + ([installed] if installed.is_dir() and installed != ROOT else [])):
            os.chdir(cwd)
            self.assertTrue(L.is_live("state"), cwd)                      # the name alone is live from here
            top = self.tree(self.tmp / f"walk-{i}")
            seen = {os.path.relpath(d, top) for d, _, _, _ in os.fwalk(top)}
            self.assertEqual(seen, {".", "state", "state/gaming", "a", "a/state", "b"}, cwd)
            self.assertEqual({d for d, _, _, _ in os.fwalk("state", dir_fd=self.fd(top))}, {"state", "state/gaming"})
            if "dir_fd" in shutil.rmtree.__code__.co_varnames:           # 3.11+
                shutil.rmtree("state", dir_fd=self.fd(top))
                self.assertFalse((top / "state").exists(), cwd)
            shutil.rmtree(top)
            self.assertFalse(top.exists(), cwd)

    def test_2_a_walk_into_a_live_folder_is_still_refused(self):
        """The same walks, where the "state" child is live: each stops at the open, before it lists or deletes."""
        os.chdir(self.tmp)                                       # its state/ is not live: only the walker's fd can tell
        top = self.tree(self.tmp / "top")
        (self.tmp / "link").symlink_to(top / "state")
        self.addCleanup(setattr, L, "_TOPS", None)
        self.addCleanup(setattr, L, "LIVE", L.LIVE)
        L.LIVE, L._TOPS = L.LIVE + (top / "state",), None               # one more live folder, as test_2 above
        walks = {"rmtree": lambda: shutil.rmtree(top), "fwalk": lambda: list(os.fwalk(top)),
                 "fwalk by a link": lambda: list(os.fwalk(self.tmp, follow_symlinks=True)),
                 "fwalk at dir_fd": lambda: list(os.fwalk("state", dir_fd=self.fd(top)))}
        if "dir_fd" in shutil.rmtree.__code__.co_varnames:
            walks["rmtree at dir_fd"] = lambda: shutil.rmtree("state", dir_fd=self.fd(top))
        for name, walk in walks.items():
            with self.assertRaises(L.LiveState, msg=name) as e:
                walk()
            self.assertRegex(str(e.exception), r"^(open|shutil\.rmtree) /\S+/(top/state|link)\b", name)
            self.assertTrue((top / "state" / "gaming" / "2030-01-02.json").is_file(), name)
            self.assertTrue((top / "state" / "post-due.lock").is_file(), name)

    def test_3_every_walker_here_is_known(self):
        """This interpreter's walkers, by their own code: a new Python that renames the fd is caught here first."""
        steps = {c.co_name: local for c, local in L._walkers().items()}
        self.assertEqual(steps.get("_fwalk"), "dirfd" if "stack" in os._fwalk.__code__.co_varnames else "topfd")
        self.assertIn(steps.get("_rmtree_safe_fd_step", steps.get("_rmtree_safe_fd")), ("dirfd", "topfd"))
        for code, local in L._walkers().items():
            self.assertTrue(local is None or local in code.co_varnames, (code.co_name, local))


class CopyTest(unittest.TestCase):
    """End to end on a throwaway copy of the factory: its state/ is the live folder its runs must not touch."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-guardcopy-"))
        cls.cf = cls.tmp / "cf"
        shutil.copytree(ROOT / "clipfactory", cls.cf / "clipfactory", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "config", cls.cf / "config")
        shutil.copy2(ROOT / "factory.py", cls.cf / "factory.py")
        (cls.cf / "state" / "gaming").mkdir(parents=True)
        (cls.cf / "state" / "sentinel.txt").write_text("the copy's live state")
        (cls.cf / "state" / "gaming" / "keep.txt").write_text("a board would sit here")
        (cls.cf / "tests").mkdir()
        (cls.cf / "tests" / "test_0first.py").write_text(FIRST)
        (cls.cf / "tests" / "test_zlast.py").write_text(LAST)
        (cls.tmp / "t").mkdir()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_in_copy(self, *args, **env):
        """python3 <args> in the copy, as from a fresh shell (nothing pinned) plus `env`."""
        e = {k: v for k, v in os.environ.items() if k != "CLIP_FACTORY_STATE"}
        e.update(TMPDIR=str(self.tmp / "t"), **env)      # its throwaway folders go when the copy does
        return subprocess.run([sys.executable, *args], cwd=self.cf, env=e, capture_output=True, text=True, timeout=120)

    def test_1_the_2026_10_17_order_cannot_reach_it(self):
        for how in (["-m", "unittest", "discover", "-s", "tests"], ["-c", PLAIN], ["-c", REVERSED]):
            before = listing(self.cf / "state")
            r = self.run_in_copy(*how)
            self.assertEqual(r.returncode, 0, f"{how}\n{r.stdout}\n{r.stderr}")
            self.assertIn("Ran 3 tests", r.stderr, how)
            self.assertEqual(listing(self.cf / "state"), before, how)

    def test_2_an_offline_command_on_the_live_folder_stops(self):
        before = listing(self.cf / "state")
        for pinned in ({}, {"CLIP_FACTORY_STATE": str(self.cf / "state")}):
            r = self.run_in_copy("factory.py", "status", "--date", "2030-01-02", **pinned)
            self.assertNotEqual(r.returncode, 0, pinned)
            self.assertIn("never touches the live state", r.stderr, pinned)
        self.assertEqual(listing(self.cf / "state"), before)
        clone = self.tmp / "clone"                       # what the message says to do instead
        shutil.copytree(self.cf / "state", clone)
        r = self.run_in_copy("factory.py", "status", "--date", "2030-01-02", CLIP_FACTORY_STATE=str(clone))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no board", r.stdout)

    def test_3_a_real_run_is_untouched(self):
        r = self.run_in_copy("-c", REAL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("real run ok", r.stdout)
        self.assertTrue((self.cf / "state" / "real-run.txt").is_file())


class ConventionTest(unittest.TestCase):
    def test_every_test_file_goes_offline_before_it_imports_the_factory(self):
        """So a file run on its own (python3 tests/test_x.py) is a test run from its first import, too."""
        def sets_offline(node) -> bool:
            return isinstance(node, ast.Assign) and getattr(node.value, "value", None) == "1" and any(
                isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute) and t.value.attr == "environ"
                and getattr(t.slice, "value", None) == "CLIP_FACTORY_OFFLINE" for t in node.targets)

        for f in sorted((ROOT / "tests").glob("test*.py")):
            offline = False
            for node in ast.parse(f.read_text()).body:
                offline = offline or sets_offline(node)
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                if any(n.split(".")[0] in ("clipfactory", "factory") for n in names):
                    self.assertTrue(offline, f"{f.name}:{node.lineno} imports {names[0]} before it sets "
                                             f'os.environ["CLIP_FACTORY_OFFLINE"] = "1"')


if __name__ == "__main__":
    unittest.main()
