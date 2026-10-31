import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# Decline and Make again with reasons (verdicts.py, the Reason codes list Andrew picked 10-20): Make again's chooser of
# reason chips and a note (Decline keeps its prompt until the Reason codes part gives it a panel), the reasons kept in
# the board note in plain words and in learn/verdicts.jsonl with the clip they were about (written before Make again
# takes the clip off the slot), the first list's codes read as today's, an unknown reason refused before anything
# changes, the old free-text decline unchanged, factory.py decline --reasons, and Approve's verdict. Throwaway
# folders; nothing reaches the network.

import contextlib
import io
import json
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
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
from clipfactory import review as R         # noqa: E402
from clipfactory import signoff as S        # noqa: E402
from clipfactory import verdicts as VD      # noqa: E402
import factory as F                         # noqa: E402

DAY = date(2031, 7, 8)


def clip(path: Path, seconds: float) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=black:s=108x192:d={seconds}",
                    "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


class DeclineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-social-decline-"))
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

    def ready(self, *slots: str) -> None:
        root = self.gta.delivered_dirs(DAY)[0]
        for k in slots:
            d = root / f"{k.replace(':', '')} clip-{k.replace(':', '')}"
            d.mkdir(parents=True, exist_ok=True)
            shutil.copy(self.master, d / "01-x.mp4")
            for p in ("youtube", "tiktok", "facebook", "reels"):
                (d / f"{p}.txt").write_text(f"Clip at {k} | Creator\n\nWords for {p} at {k}.\n")
        K.collect(self.cfg, self.gta, DAY)

    def slot(self, k: str = "07:00") -> dict:
        return B.read(self.gta, DAY)["slots"][k]

    def verdicts(self) -> list:
        return VD.rows(self.gta)

    def serve(self):
        srv = R.Server(("127.0.0.1", 0), R.make_handler(self.cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        tok = R.token()

        def post(body):
            h = {"Host": f"127.0.0.1:{self.cfg.review_port}", "Content-Type": "application/json", "X-Token": tok,
                 "Origin": f"http://127.0.0.1:{self.cfg.review_port}", "Sec-Fetch-Site": "same-origin"}
            req = urllib.request.Request(f"http://127.0.0.1:{port}/act", data=json.dumps(body).encode(), method="POST",
                                         headers=h)
            try:
                with direct.open(req, timeout=30) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        return post

    def test_1_make_again_has_the_chooser_decline_has_the_reason_codes_panel(self):
        self.ready("07:00")
        page = R.page(self.cfg, DAY, self.cfg.slot_time(DAY, "06:00"), tok="t")
        self.assertIn("const REASONS=" + json.dumps(VD.CHIPS).replace("</", "<\\/") + ";", page)
        self.assertIn("if(op==='remake'){openChooser(b,op,account,slot);return}", page)
        self.assertNotIn("prompt('Decline", page)              # Decline: the Reason codes panel (reasons.py), one tap
        self.assertIn('data-op="why" data-why="decline"', page)
        self.assertIn("function openChooser(b,op,account,slot)", page)
        self.assertIn("reasons:[...picked],remake}", page)
        self.assertIn('data-op="decline"', page)
        self.assertIn('data-op="remake"', page)

    def test_2_make_again_records_the_clip_before_it_goes(self):
        self.ready("07:00")
        s = self.slot()
        with EX.edit(self.cfg, self.gta, DAY, "07:00", self.who, "first comment") as x:
            x["first_comment"] = {"facebook_reels": {"text": "Full video: https://youtu.be/x", "from": "edited"}}
        xp = Path(self.slot()["extras"]["path"])
        lines = S.decline(self.cfg, self.gta, DAY, ["07:00"], self.who, "", remake=True,
                          reasons=["weak_hook", "framing", "weak_hook"])
        self.assertEqual(lines, ["  07:00: open again, the bot will make a new clip"])
        after = self.slot()
        self.assertEqual(after["status"], B.OPEN)
        for k in ("clip", "copy", "slug", "folder", "extras"):
            self.assertNotIn(k, after)
        self.assertFalse(xp.exists())
        self.assertTrue(B.last_note(after).endswith(f"sent back to be made again by {self.who} "
                                                    "(reasons: hook, framing)"))
        (row,) = self.verdicts()
        self.assertEqual((row["verdict"], row["by"], row["board"], row["slot"], row["reasons"], row["note"]),
                         ("remake", self.who, DAY.isoformat(), "07:00", ["hook", "framing"], ""))   # old code read as hook
        self.assertEqual((row["slug"], row["clip_sha"]), (s["slug"], s["clip"]["sha256"]))

    def test_3_an_unknown_reason_is_refused_before_anything_changes(self):
        self.ready("07:00")
        post = self.serve()
        code, d = post({"op": "decline", "account": "gaming", "date": DAY.isoformat(), "slots": ["07:00"],
                        "reason": "", "reasons": ["weak_hook", "dull"]})
        self.assertEqual(code, 400)
        self.assertIn("'dull' is not a reason", d["message"])
        self.assertEqual(self.slot()["status"], B.READY)
        self.assertEqual(self.verdicts(), [])
        code, d = post({"op": "decline", "account": "gaming", "date": DAY.isoformat(), "slots": ["07:00"],
                        "reason": "too dark", "reasons": ["audio"]})
        self.assertEqual((code, d["ok"]), (200, True))
        self.assertTrue(B.last_note(self.slot()).endswith(f"declined by {self.who}: too dark (reasons: audio)"))
        self.assertEqual([(r["verdict"], r["reasons"], r["note"]) for r in self.verdicts()],
                         [("decline", ["audio"], "too dark")])

    def test_4_the_old_free_text_decline_is_unchanged(self):
        self.ready("07:00", "08:45")
        S.decline(self.cfg, self.gta, DAY, ["07:00"], self.who, "too long")
        self.assertEqual(B.last_note(self.slot()), f"declined by {self.who}: too long")
        S.decline(self.cfg, self.gta, DAY, ["08:45"], self.who, "", remake=True)
        self.assertEqual(B.last_note(self.slot("08:45")), f"sent back to be made again by {self.who}")
        self.assertEqual([(r["verdict"], r["reasons"], r["note"]) for r in self.verdicts()],
                         [("decline", [], "too long"), ("remake", [], "")])

    def test_5_the_command_line_takes_reasons(self):
        self.ready("07:00")
        buf = io.StringIO()
        with mock.patch.object(C, "load", lambda *a: self.cfg), contextlib.redirect_stdout(buf):
            F.main(["decline", "gaming", "--date", DAY.isoformat(), "--slots", "07:00", "--by", self.who,
                    "--reason", "flat", "--reasons", "slow_start,TEXT,wrong-name"])
        self.assertIn("07:00: declined", buf.getvalue())
        self.assertTrue(B.last_note(self.slot()).endswith("flat (reasons: hook, text, wrong name)"))
        self.assertEqual(self.verdicts()[-1]["reasons"], ["hook", "text", "wrong-name"])

    def test_6_approve_writes_a_verdict_and_a_broken_record_never_stops_it(self):
        self.ready("07:00", "08:45")
        S.approve(self.cfg, self.gta, DAY, ["07:00"], self.who)
        (row,) = self.verdicts()
        self.assertEqual((row["verdict"], row["slot"], row["reasons"]), ("approve", "07:00", []))
        with mock.patch.object(VD, "record", side_effect=OSError("disk full")):
            S.approve(self.cfg, self.gta, DAY, ["08:45"], self.who)
            S.pull(self.cfg, self.gta, DAY, ["08:45"], self.who)
            S.decline(self.cfg, self.gta, DAY, ["08:45"], self.who, "", reasons=["rights"])
        self.assertEqual(self.slot("08:45")["status"], B.DECLINED)
        self.assertIn("verdicts:record", (C.STATE / "logs" / "social-errors.log").read_text())

    def test_7_a_clients_clips_leave_no_verdict(self):
        self.ready("07:00")
        client = C.Account(**{**self.gta.__dict__, "raw": {**self.gta.raw, "owner": "client"}})
        self.assertIsNone(VD.record(self.cfg, client, DAY, self.slot(), "decline", self.who, ["audio"]))
        self.assertEqual(self.verdicts(), [])

    def test_8_one_list_the_reason_codes_and_the_old_rows_read_as_them(self):
        """Andrew's pick, 2026-10-20: the Reason codes list is the only list; rows of the first list still count."""
        self.assertEqual(VD.REASONS, ("wrong-name", "framing", "cut", "audio", "length", "repeat", "incomplete",
                                      "text", "hook", "rights", "other"))
        self.assertEqual(VD.HARD, ("wrong-name", "framing", "cut", "audio", "length", "repeat"))
        self.assertEqual(VD.check(["WRONG NAME", "wrong_person", "Cut", "too_short", "length"]),
                         ["wrong-name", "cut", "length"])
        self.assertEqual(VD.normalize(["weak_hook", "slow_start", "boring_middle", "no-such", "captions"]),
                         ["hook", "other", "text"])
        self.assertEqual(VD.words(["hard_to_follow", "rights", "made-up"]), "incomplete, rights, made-up")  # never raises
        self.assertIn("pull", VD.VERDICTS)
        self.assertEqual(set(VD.OLD.values()) - set(VD.REASONS), set())


if __name__ == "__main__":
    unittest.main()
