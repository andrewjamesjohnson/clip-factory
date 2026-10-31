"""The run journal (the owner, 2026-10-30, the loops thesis item 6). Throwaway folders; no network."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ.setdefault("CLIP_FACTORY_STATE", _tempfile.mkdtemp(prefix="clipfactory-journal-state-"))

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import factory                           # noqa: E402
from clipfactory import config as C      # noqa: E402
from clipfactory import journal as J     # noqa: E402


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-journal-"))
        self.old_state, C.STATE = C.STATE, self.tmp / "state"

    def tearDown(self):
        C.STATE = self.old_state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rows(self):
        return [json.loads(x) for x in J.path(datetime.now().date()).read_text().splitlines()]

    def test_record_read_jobs_doctor(self):
        self.assertIsNone(J.doctor()[0][0])
        for st in ("ok", "exit", "failed", "failed"):
            J.record("social tick", st, code=0 if st == "ok" else 1, seconds=1.5)
        J.record("task clip-coach", "started", why="go")
        js = J.jobs(J.read())
        self.assertEqual((js["social tick"]["runs"], js["social tick"]["bad"], js["social tick"]["in_a_row"]), (4, 3, 3))
        rows = J.doctor()
        self.assertTrue(any(g is False and "social tick" in t for g, t, _ in rows))
        self.assertTrue(any(g is None and "clip-coach" in t for g, t, _ in rows))
        J.record("social tick", "ok", code=0)
        self.assertFalse(any(g is False for g, _, _ in J.doctor()))
        old = (datetime.now().astimezone() - timedelta(hours=30)).isoformat(timespec="seconds")
        with J.path(datetime.now().date()).open("a") as f:
            f.write(json.dumps({"at": old, "job": "stale", "status": "failed"}) + "\nnot json\n")
        self.assertNotIn("stale", J.jobs(J.read()))

    def test_record_never_raises(self):
        with mock.patch.object(J.store, "path", side_effect=OSError("disk gone")):
            J.record("x", "ok")

    def test_main_journals_launchd_runs_only(self):
        with mock.patch.dict(_os.environ, {"CLIP_FACTORY_LOG_STAMP": ""}), mock.patch.object(factory.os, "getppid",
                                                                                              return_value=4242):
            factory.main(["journal", "jobs"])
        self.assertFalse(J.path(datetime.now().date()).exists())
        with mock.patch.dict(_os.environ, {"CLIP_FACTORY_LOG_STAMP": "1"}):
            factory.main(["journal", "jobs", "--hours", "1"])
            with self.assertRaises(SystemExit):
                factory.main(["changes", "add", "--id", "Bad Id"])
        got = [(r["job"], r["status"]) for r in self.rows()]
        self.assertEqual(got, [("journal jobs", "ok"), ("changes add", "exit")])
        with mock.patch.dict(_os.environ, {"CLIP_FACTORY_LOG_STAMP": "1", "CLIP_FACTORY_JOURNAL": "0"}):
            factory.main(["journal", "jobs"])
        self.assertEqual(len(self.rows()), 2)


if __name__ == "__main__":
    unittest.main()
