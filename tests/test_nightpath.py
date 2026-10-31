"""The night's path and the idea politics read by code (the owner, 2026-10-31, the loops thesis items 19 and 8).
Throwaway folders; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-nightpath-state-"))

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import ideasfile as IF    # noqa: E402
from clipfactory import nightpath as NP    # noqa: E402

IDEAS = """# Politics · Ideas

## DEAD

| Idea | Why it died | Only retry if | Date |
| --- | --- | --- | --- |
| Gold on covers | Andrew: "Stop with the gold" | never | 10.30 |
| THE EXPLAINER | Those suck | Andrew asks for it again | 10.28 |

## TESTING

| Idea | How it is tested | Result due |
| --- | --- | --- |
| Pre-hook | e-0001 | 12 a side |
"""


def use(cmd: str) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                                     "input": {"command": cmd}}]}}) + "\n"


class NightPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-nightpath-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_file(self, name: str, cmds, helper_cmds=()):
        p = self.tmp / f"{name}.jsonl"
        p.write_text("".join(use(c) for c in cmds))
        if helper_cmds:
            h = self.tmp / name / "subagents" / "agent-1.jsonl"
            h.parent.mkdir(parents=True)
            h.write_text("".join(use(c) for c in helper_cmds))
        return str(p)

    def test_steps_found_in_the_run_and_its_helpers(self):
        good = self.run_file("a", ["python3 $F shift begin politics", "factory.py daily8", "python3 $F shift check"],
                             ["factory.py learn brief politics", "python3 $F shift end politics"])
        bad = self.run_file("b", ["factory.py shift wait gaming", "factory.py learn brief gaming"])
        empty = self.run_file("c", [])
        files = {good: {"task": "hearing-clip-sweep-2"}, bad: {"task": "gaming-morning-sweep"},
                 empty: {"task": "politics-early-gap-fill"}, str(self.tmp / "chat.jsonl"): {"task": "chat"}}
        rs = {r["task"]: r for r in NP.runs(36, files=files)}
        self.assertEqual(rs["hearing-clip-sweep-2"]["missing"], [])
        self.assertEqual(rs["gaming-morning-sweep"]["missing"], ["shift begin", "shift end"])
        self.assertNotIn("politics-early-gap-fill", rs)               # never called a tool: not judged here
        rows = NP.doctor(files=files)
        self.assertTrue(any(g is False and "gaming-morning-sweep" in t for g, t, _ in rows))
        self.assertTrue(any(g is True and "hearing-clip-sweep-2" in t for g, t, _ in rows))
        self.assertIsNone(NP.doctor(files={})[0][0])

    def test_the_idea_politics_lines(self):
        acct = mock.Mock()
        acct.name = "Politics"
        (self.tmp / "Politics").mkdir()
        (self.tmp / "Politics" / "IDEAS.md").write_text(IDEAS)
        with mock.patch.dict(_os.environ, {IF.ROOT_ENV: str(self.tmp)}):
            self.assertEqual(IF.dead(acct), [("Gold on covers", "never"), ("THE EXPLAINER", "Andrew asks for it again")])
            ls = IF.lines(acct)
            self.assertIn("IDEAS.md", ls[0])
            self.assertIn("THE EXPLAINER (only retry if: Andrew asks for it again)", ls[2])
            acct.name = "No Such Account"
            self.assertEqual(IF.lines(acct), [])


if __name__ == "__main__":
    unittest.main()
