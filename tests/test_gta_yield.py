import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""GTA maker yield (audit gta-yield, 2026-10-18), offline: LENGTH and unreadable preflight refusals try the next
candidate (TOOL never does); a failed or timed-out intake is noted on its inbox row and given up after 1 timeout or 2
failures; a source over 30 minutes is dealt last, the night runs its sources shortest first and starts no intake that
would end after 01:45; the worker makes up to 3 passes until 01:45 and never deals a failed source again that day;
a hand cut credited to its own job drops the plan's picks; a clip refused for its length leaves its slot fillable and
is never taken. Review fixes (10-18): a failed intake counts against its upload only when an intake of its pass or a
later one worked, and a pass whose every intake failed ends the passes (a night with no network blames no video); no
step of the night starts after render_until 02:30; an upload of unknown length counts as an hour; a slot failed late
keeps a clip dropped in meanwhile; collect's own failures carry fail_kind. No skill runs: run.py is faked; the
collect tests draw 0.5-3 s 108x192 clips with ffmpeg at nice 19.
The night shift (2026-10-19) moved the night's clock (night_until and pass_until 05:30, render_until 05:50, 8 passes,
stop_at 06:15; a worker out of sources rescans every 40 minutes until 03:20): these tests keep the clock they were
written on through accounts.json "gta_maker" (Yield.CLOCK, rescans off), and tests/test_night_shift.py pins the new
defaults."""
import argparse
import contextlib
import io
import json
import shutil
import subprocess
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from clipfactory import board as B      # noqa: E402
from clipfactory import collect as K    # noqa: E402
from clipfactory import config as C     # noqa: E402
from clipfactory import makers as M     # noqa: E402
from clipfactory import nightretry as NR    # noqa: E402
from clipfactory import signoff as S    # noqa: E402
from test_gta_maker_fixes import DAY, Base, cand, row   # noqa: E402

CANDS = [cand(i, i * 100, i * 100 + 40) for i in range(1, 7)]
LENGTH_AT_1X = ("\n== preflight (at 1.0x)\n   $ python3 preflight.py s\n    [FAIL] LENGTH   28.4s delivered\n"
                "   -- 4s, exit 1\n\nSTOPPED at 'preflight (at 1.0x)' (exit 1); the whole output is in RUN.md")
SCROLLED_OUT = ("\n== preflight\n   $ python3 preflight.py lucia\n    [ok  ] SET      one picture\n"
                "    [ok  ] DAY      one reel source today\n\nNOT READY: lucia. Fix the slate, re-run preflight, then "
                "render.\n   -- 41s, exit 1\n\nSTOPPED at 'preflight' (exit 1); the whole output is in RUN.md")
TOOL_VISIBLE = ("\n== preflight\n    [FAIL] TOOL     the tool has UNCOMMITTED changes: SKILL.md\n"
                "\nNOT READY: the tool itself. Nothing below was checked.\n\nSTOPPED at 'preflight' (exit 1)")
TOOL_NOT_READY_ONLY = "\n== preflight\n\nNOT READY: the tool itself. Nothing below was checked.\n\nSTOPPED at 'preflight' (exit 1)"
TOOL_MOVED = ("\n== preflight\n      ab12cd34 -> ef56ab78\n    the tool moved since this job was pinned: re-pin\n"
              "\nSTOPPED at 'preflight' (exit 1)")


from politics_slots import pin_politics  # noqa: E402  the Politics day these tests were written on

def local(cfg: C.Config, d: date, hhmm: str) -> datetime:
    return cfg.slot_time(d, hhmm)


class Yield(Base):
    """make_gta / make_passes with run.py faked per source: intake[url] = exit code or 'timeout'; picks[n] = (exit,
    output) for every job; collect marks the slot it is given ready."""
    # the clock these tests were written on (10-18), set as the account's own gta_maker (the night shift's defaults
    # are pinned in test_night_shift.py)
    CLOCK = {"night_until": "01:45", "pass_until": "01:45", "render_until": "02:30", "passes": 3, "stop_at": "",
             "rescan_every": 0}

    def fresh(self):
        super().fresh()
        self.reload(**self.CLOCK)

    def night(self, intake=None, picks=None, moments=(4000, CANDS), only=None, passes=False, now=None, clock=None,
              advance=None, scan_rc=0):
        intake, picks, calls, advance = intake or {}, picks or {}, [], advance or {}
        self.env_at_intake = []

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            calls.append(list(cmd))
            out, rc = "", 0
            if cmd[1] == "watch.py":
                rc = scan_rc
            if cmd[1] == "run.py" and "--job" in cmd:                     # an intake: run.py <url> ... --job J
                self.env_at_intake.append(os.environ.get("CLIP_RENDERLOCK_UNTIL"))      # the run's start deadline
                if clock is not None and cmd[2] in advance:
                    clock[0] += advance[cmd[2]]                                # the intake took that long
                how = intake.get(cmd[2], 0)
                if how == "timeout":
                    raise subprocess.TimeoutExpired(cmd, 4 * 3600)
                rc, out = how if isinstance(how, tuple) else (int(how), "ERROR: [youtube] 403 Forbidden" if how else "")
                if rc == 0 and moments is not None:
                    d = Path(self.acct.skill_dir) / "work" / cmd[cmd.index("--job") + 1]
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "moments.json").write_text(json.dumps({"seconds": moments[0], "candidates": moments[1]}))
            elif cmd[1] == "run.py" and "--pick" in cmd:
                rc, out = picks.get(int(cmd[cmd.index("--pick") + 1]), (0, ""))
            with open(log, "a") as f:
                f.write(f"\n$ {' '.join(cmd)}\n{out}\n")
            return subprocess.CompletedProcess(cmd, rc)

        def fake_collect(cfg, acct, day, extra=None, only=None):
            if only:
                with B.edit(cfg, acct, day) as bd:
                    for k in only:
                        if bd["slots"][k]["status"] in B.FILLABLE:
                            bd["slots"][k]["status"] = B.READY
                return [f"  {only[0]} x: ready, 40.0s"]
            return []

        said = []
        if now is not None:
            self.cfg.now = lambda: now                                     # the clock the maker reads
        if clock is not None:
            self.cfg.now = lambda: clock[0]
        with B.edit(self.cfg, self.acct, DAY):
            pass
        with mock.patch.object(M, "run_logged", fake_run), \
                mock.patch.object(M, "pack_gta_slot", lambda a, d, s, l, e: f"  {s}: packed"), \
                mock.patch.object(M.K, "collect", fake_collect):
            if passes:
                n = M.make_passes(self.cfg, self.acct, DAY, only, say=said.append)
            else:
                said = M.make_gta(self.cfg, self.acct, DAY, only=only)
                n = 1
        return calls, said, n

    @staticmethod
    def intakes(calls) -> list:
        return [c[2] for c in calls if c[1] == "run.py" and "--job" in c]

    def row_of(self, url: str) -> dict:
        items = json.loads((self.acct.account_dir / "Publish" / "inbox.json").read_text())["items"]
        return items[M.video_id(url)]

    FOUR = ["07:00", "08:45", "10:30", "12:15"]          # clips_per_source 3: the first source 3 slots, the next 1


# ------------------------------------------------------------------------------- (1) which refusals retry
class Retries(Yield):
    def test_length_still_short_at_1x_tries_the_next_candidate(self):
        self.inbox([row(1)])
        calls, lines, _ = self.night(picks={1: (1, LENGTH_AT_1X)}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2], lines)
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)
        self.assertEqual(M.NOT_RETRIED, {"TOOL"})

    def test_a_stop_whose_gates_scrolled_out_tries_the_next_candidate(self):
        self.assertEqual(M.preflight_refusal(SCROLLED_OUT), [])               # stopped at preflight, gates unread
        self.inbox([row(1)])
        calls, lines, _ = self.night(picks={1: (1, SCROLLED_OUT)}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2], lines)
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["status"], B.READY)
        self.assertTrue(any("its gates scrolled out of the log" in n for n in s["notes"]), s["notes"])

    def test_the_tool_itself_is_never_retried(self):
        for out in (TOOL_VISIBLE, TOOL_NOT_READY_ONLY, TOOL_MOVED):
            self.fresh()
            self.inbox([row(1)])
            calls, _, _ = self.night(picks={1: (1, out)}, only=["07:00"])
            self.assertEqual(self.picks_of(calls), [1], out)
            s = self.board()["slots"]["07:00"]
            self.assertEqual((s["status"], s["fail_kind"]), (B.FAILED, "tool"), out)

    def test_retries_are_still_capped(self):
        self.inbox([row(1)])
        calls, _, _ = self.night(picks={n: (1, LENGTH_AT_1X) for n in range(1, 7)}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1, 2, 3])                     # retries 2: two more, then it fails
        s = self.board()["slots"]["07:00"]
        self.assertEqual((s["status"], s["fail_kind"]), (B.FAILED, "refused"))

    def test_a_render_stop_is_not_retried(self):
        self.inbox([row(1)])
        calls, _, _ = self.night(picks={1: (1, "STOPPED at 'the render (build_all.py)' (exit 1)")}, only=["07:00"])
        self.assertEqual(self.picks_of(calls), [1])
        self.assertEqual(self.board()["slots"]["07:00"]["fail_kind"], "run")


# ------------------------------------------------------------------------------- (2) failed intakes
class FailedIntakes(Yield):
    def test_a_failed_intake_is_noted_on_its_row_and_nothing_else_changes(self):
        a, b = row(1), row(2)
        p = self.inbox([a, b])
        before = json.loads(p.read_text())["items"]
        calls, lines, _ = self.night(intake={a["url"]: 1}, only=self.FOUR)     # a: 07:00-10:30, b: 12:15 works
        self.assertEqual(self.intakes(calls), [a["url"], b["url"]])
        got = json.loads(p.read_text())["items"]
        f = got[M.video_id(a["url"])]["intake_failed"]
        self.assertEqual((f["n"], f["timeouts"]), (1, 0))
        self.assertIn("403", f["last_error"])
        self.assertRegex(f["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        cut = ("status", "cut_for", "cut_job", "cut_at")                       # b's own mark (mark_inbox_cut)
        self.assertEqual({k: v for k, v in got[M.video_id(b["url"])].items() if k not in cut},
                         {k: v for k, v in before[M.video_id(b["url"])].items() if k not in cut})
        self.assertNotIn("intake_failed", got[M.video_id(b["url"])])
        self.assertEqual({k: v for k, v in got[M.video_id(a["url"])].items() if k != "intake_failed"},
                         before[M.video_id(a["url"])])
        self.assertEqual(list(p.parent.glob("*.factory-tmp")), [])
        self.assertTrue(any("held against this upload only if another intake" in ln for ln in lines), lines)
        self.assertTrue(any("intake failure 1 noted" in ln for ln in lines), lines)
        s = self.board()["slots"]["07:00"]
        self.assertEqual((s["status"], s["fail_kind"]), (B.FAILED, "intake"))
        self.assertEqual(s["sources_tried"], [a["url"]])

    def test_a_timeout_is_noted_and_the_row_is_given_up(self):
        a = row(1)
        self.inbox([a])
        _, lines, _ = self.night(intake={a["url"]: "timeout"}, only=["07:00"])
        f = self.row_of(a["url"])["intake_failed"]
        self.assertEqual((f["n"], f["timeouts"]), (1, 1))
        self.assertIn("ran past 4 hours", f["last_error"])
        self.assertTrue(any("not cut again" in ln for ln in lines), lines)
        self.assertEqual(M.gta_sources(self.acct, set()), [])
        self.assertEqual(self.board()["slots"]["07:00"]["fail_kind"], "timeout")

    def test_two_failures_or_one_timeout_give_a_row_up(self):
        st = M.gta_settings(self.acct)
        self.assertFalse(M.intake_gave_up({"intake_failed": {"n": 1, "timeouts": 0}}, st))
        self.assertTrue(M.intake_gave_up({"intake_failed": {"n": 2, "timeouts": 0}}, st))
        self.assertTrue(M.intake_gave_up({"intake_failed": {"n": 1, "timeouts": 1}}, st))
        self.assertFalse(M.intake_gave_up({"intake_failed": "garbage"}, st))
        self.assertFalse(M.intake_gave_up({}, st))
        self.reload(intake_fails_max=3)
        self.assertFalse(M.intake_gave_up({"intake_failed": {"n": 2}}, M.gta_settings(self.acct)))
        self.inbox([row(1, intake_failed={"n": 1, "timeouts": 0}), row(2, intake_failed={"n": 3})])
        self.assertEqual([r["url"] for r in M.gta_sources(self.acct, set())], [row(1)["url"]])
        self.assertEqual(M.given_up(self.acct), 1)

    def test_a_failed_source_is_not_dealt_again_that_day_but_may_be_on_another(self):
        a, b, c = row(1), row(2), row(3)
        self.inbox([a, b, c])
        self.night(intake={a["url"]: 1}, only=self.FOUR)                      # b works: a's failure counts
        self.assertEqual(self.row_of(a["url"])["intake_failed"]["n"], 1)
        self.assertIn(a["url"], self.board()["slots"]["07:00"]["sources_tried"])
        self.assertIn(M.video_id(a["url"]), M.tried_on(self.board()))
        calls, _, _ = self.night(only=["07:00"])                              # the 03:45 retry, the same day
        self.assertEqual(self.intakes(calls), [c["url"]])
        self.assertEqual(self.board()["slots"]["07:00"]["sources_tried"], [a["url"], c["url"]])
        self.assertEqual(self.board()["slots"]["07:00"]["source_url"], c["url"])   # the re-deal overwrote it ...
        today = M.used_sources(self.acct) | M.tried_on(self.board())
        self.assertNotIn(a["url"], [r["url"] for r in M.gta_sources(self.acct, today)])   # ... tried_on keeps it out
        # another day: one failure is not two, so it may be tried again there (08:45 and 10:30 still name a, failed
        # with no clip: that is no cut source)
        self.assertEqual(self.board()["slots"]["08:45"]["source_url"], a["url"])
        self.assertIn(a["url"], [r["url"] for r in M.gta_sources(self.acct, M.used_sources(self.acct))])

    def test_an_intake_the_tool_itself_stopped_is_not_held_against_the_source(self):
        a = row(1)
        self.inbox([a])
        _, lines, _ = self.night(intake={a["url"]: (1, "[FAIL] TOOL     the tool has UNCOMMITTED changes")},
                                 only=["07:00"])
        self.assertNotIn("intake_failed", self.row_of(a["url"]))
        self.assertTrue(any("intake FAILED (the tool itself)" in ln for ln in lines), lines)
        self.assertTrue(any("stopped by the tool itself or not started" in ln for ln in lines), lines)
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["fail_kind"], "tool")
        self.assertEqual(s["sources_tried"], [])                              # the next run may deal it again

    def test_no_inbox_row_or_file_says_so(self):
        self.assertIn("unreadable", M.mark_inbox_failed(self.acct, row(1), "x"))
        self.inbox([row(2)])
        self.assertIn("no row", M.mark_inbox_failed(self.acct, row(1), "x"))


# ------------------------------------------------------------------------------- (3) long sources
class LongSources(Yield):
    def todo(self, n: int) -> list:
        return [{"slot": self.acct.slots[i], "format": "underlay", "status": B.OPEN} for i in range(n)]

    def test_a_long_row_is_dealt_only_after_every_shorter_one(self):
        long_ = row(1, duration=4497)                                         # the best score, but 75 minutes
        short = [row(n, duration=900) for n in (2, 3, 4, 5)]
        for spread in (False, True):
            self.reload(spread=spread)
            plan, _ = M.plan_gta(self.acct, self.todo(10), [long_] + short)
            self.assertNotIn(long_["url"], [i["url"] for i, _, _ in plan], spread)     # 4 x 3 covers 10
            plan, _ = M.plan_gta(self.acct, self.todo(10), [long_] + short[:3])
            by = {i["url"]: [s["slot"] for s in ss] for i, ss, _ in plan}
            self.assertEqual(sum(len(v) for k, v in by.items() if k != long_["url"]), 9, spread)
            self.assertEqual(len(by[long_["url"]]), 1, spread)                 # only the slot nothing shorter covers
        self.reload(max_source_s=0)                                            # no cap: by score, as before
        plan, _ = M.plan_gta(self.acct, self.todo(3), [long_] + short)
        self.assertEqual(plan[0][0]["url"], long_["url"])

    def test_the_night_runs_its_sources_shortest_first(self):
        rows = [row(1, duration=1500), row(2, duration=300), row(3, duration=800)]
        self.inbox(rows)
        calls, _, _ = self.night(only=["07:00", "08:45", "10:30", "12:15", "14:00", "15:45", "17:30"])
        self.assertEqual(self.intakes(calls), [rows[1]["url"], rows[2]["url"], rows[0]["url"]])

    def test_no_intake_starts_that_would_end_after_0145(self):
        late = local(self.cfg, DAY - timedelta(days=1), "23:30")
        self.inbox([row(1, duration=1741)])                                   # x4 = 1h56: ends 01:26
        calls, _, _ = self.night(only=["07:00"], now=late)
        self.assertEqual(len(self.intakes(calls)), 1)
        self.fresh()
        self.inbox([row(1, duration=1790)])                                   # x4 = 1h59: ends 01:29 at 23:30 ...
        calls, lines, _ = self.night(only=["07:00"], now=local(self.cfg, DAY, "00:10"))   # ... 02:09 at 00:10
        self.assertEqual(self.intakes(calls), [])
        self.assertIn("not dealt now: Creator 1, upload 1: 30 min long, its intake would end about 02:09, after "
                      "01:45 (night_until); left for a later run", lines)
        self.assertEqual(lines[-1], "no waiting upload fits the time left: nothing to cut now")
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["status"], B.OPEN)
        self.assertNotIn("sources_tried", s)
        self.fresh()
        self.inbox([row(1, duration=4497)])                                   # after 01:45 (the 03:45 retry): no guard
        calls, _, _ = self.night(only=["07:00"], now=local(self.cfg, DAY, "03:45"))
        self.assertEqual(len(self.intakes(calls)), 1)
        self.fresh()
        self.inbox([row(1, duration=None)])                                   # unknown length: an hour, x4 = 4 h
        calls, lines, _ = self.night(only=["07:00"], now=late)
        self.assertEqual(self.intakes(calls), [])
        self.assertTrue(any("length unknown (counted as 60 min), its intake would end about 03:30" in ln
                            for ln in lines), lines)
        calls, _, _ = self.night(only=["07:00"], now=local(self.cfg, DAY - timedelta(days=1), "21:00"))
        self.assertEqual(len(self.intakes(calls)), 1)                         # alone at 21:00: ends about 01:00
        self.fresh()
        self.reload(unknown_source_s=0)                                       # the switch: never stopped, as before
        self.inbox([row(1, duration=None)])
        calls, _, _ = self.night(only=["07:00"], now=late)
        self.assertEqual(len(self.intakes(calls)), 1)

    def test_an_upload_of_unknown_length_is_dealt_after_every_known_one(self):
        unknown, short = row(1, duration=None), [row(n, duration=900) for n in (2, 3, 4)]
        plan, _ = M.plan_gta(self.acct, self.todo(9), [unknown] + short)
        self.assertNotIn(unknown["url"], [i["url"] for i, _, _ in plan])        # 3 x 3 covers 9
        plan, _ = M.plan_gta(self.acct, self.todo(10), [unknown] + short)
        by = {i["url"]: [s["slot"] for s in ss] for i, ss, _ in plan}
        self.assertEqual(len(by[unknown["url"]]), 1)                           # only the slot nothing else covers
        self.assertEqual(sorted([unknown] + short, key=M.shortest_first)[-1]["url"], unknown["url"])

    def test_a_run_with_a_start_deadline_starts_no_intake_that_would_end_after_it(self):
        by = str(int(local(self.cfg, DAY, "04:50").timestamp()))          # the 03:45 retry's CLIP_RENDERLOCK_UNTIL
        self.assertEqual(M.GTA_MAKER_DEFAULTS["deadline_x"], 3)
        self.inbox([row(1, duration=1500)])                               # x3 = 75 min: 05:00
        with mock.patch.dict(os.environ, {"CLIP_RENDERLOCK_UNTIL": by}):
            calls, lines, _ = self.night(only=["07:00"], now=local(self.cfg, DAY, "03:45"))
        self.assertEqual(self.intakes(calls), [])
        self.assertTrue(any("after this run's start deadline 04:50 (CLIP_RENDERLOCK_UNTIL)" in ln for ln in lines),
                        lines)
        self.fresh()
        self.inbox([row(1, duration=1200)])                               # x3 = 60 min: 04:45 (x4 would refuse it)
        with mock.patch.dict(os.environ, {"CLIP_RENDERLOCK_UNTIL": by}):
            calls, _, _ = self.night(only=["07:00"], now=local(self.cfg, DAY, "03:45"))
        self.assertEqual(len(self.intakes(calls)), 1)
        self.fresh()
        self.reload(deadline_x=0)                                         # the switch: no start-deadline guard
        self.inbox([row(1, duration=1500)])
        with mock.patch.dict(os.environ, {"CLIP_RENDERLOCK_UNTIL": by}):
            calls, _, _ = self.night(only=["07:00"], now=local(self.cfg, DAY, "03:45"))
        self.assertEqual(len(self.intakes(calls)), 1)

    def test_night_time(self):
        self.assertEqual(M.night_time(self.cfg, DAY, "01:45"), local(self.cfg, DAY, "01:45"))
        self.assertEqual(M.night_time(self.cfg, DAY, "23:30"), local(self.cfg, DAY - timedelta(days=1), "23:30"))


# ------------------------------------------------------------------------------- (4) the night's passes
class Passes(Yield):
    def test_pass_two_deals_a_new_source_to_the_slots_a_failed_intake_left(self):
        a, b = row(1), row(2)
        self.inbox([a, b])
        calls, said, n = self.night(intake={a["url"]: 1}, only=["07:00", "08:45"], passes=True)
        self.assertEqual(n, 2, said)               # one failed intake alone does not end the passes
        self.assertEqual(self.intakes(calls), [a["url"], b["url"]])
        self.assertTrue(any(ln.startswith("== pass 2 of 3: 07:00, 08:45 open or failed") for ln in said), said)
        bd = self.board()["slots"]
        self.assertEqual([bd[k]["status"] for k in ("07:00", "08:45")], [B.READY, B.READY])
        self.assertEqual(bd["07:00"]["source_url"], b["url"])
        self.assertEqual(bd["07:00"]["sources_tried"], [a["url"], b["url"]])
        self.assertEqual(self.row_of(a["url"])["intake_failed"]["n"], 1)     # b worked in a later pass: a counts

    def test_a_failed_source_is_never_dealt_again_in_a_later_pass(self):
        a, b, c, d = row(1), row(2), row(3), row(4)
        self.inbox([a, b, c, d])
        calls, said, n = self.night(intake={a["url"]: 1, c["url"]: 1}, only=self.FOUR, passes=True)
        # pass 1: a fails (07:00-10:30), b works (12:15); pass 2: c fails alone; pass 3: d
        self.assertEqual((n, self.intakes(calls)), (3, [a["url"], b["url"], c["url"], d["url"]]), said)
        s = self.board()["slots"]["07:00"]
        self.assertEqual((s["status"], s["sources_tried"]), (B.READY, [a["url"], c["url"], d["url"]]))
        self.assertEqual([self.row_of(u).get("intake_failed", {}).get("n") for u in (a["url"], c["url"])], [1, 1])

    def test_at_most_passes_in_all(self):
        rows = [row(i) for i in range(1, 5)]
        self.inbox(rows)
        fail = {rows[i]["url"]: 1 for i in (0, 2, 3)}                         # rows[1] works in pass 1
        calls, said, n = self.night(intake=fail, only=self.FOUR, passes=True)
        self.assertEqual((n, len(self.intakes(calls))), (3, 4), said)
        self.assertIn("no pass 4: 3 passes made (gta_maker passes)", said)
        self.fresh()
        self.reload(passes=1)
        self.inbox(rows)
        calls, said, n = self.night(intake=fail, only=self.FOUR, passes=True)
        self.assertEqual((n, len(self.intakes(calls))), (1, 2))

    def test_no_pass_starts_after_pass_until(self):
        a, b = row(1, duration=300), row(2, duration=300)       # x3 = 15 min: in before render_until 02:30
        self.inbox([a, b])
        calls, said, n = self.night(intake={a["url"]: 1}, only=["07:00"], passes=True,
                                    now=local(self.cfg, DAY, "01:50"))
        self.assertEqual((n, self.intakes(calls)), (1, [a["url"]]))
        self.assertTrue(any("no pass 2: it is past 01:45 (pass_until)" in ln for ln in said), said)

    def test_the_passes_stop_when_no_source_is_left(self):
        a = row(1)
        self.inbox([a])
        calls, said, n = self.night(intake={a["url"]: 1}, only=["07:00", "08:45"], passes=True)
        self.assertEqual(n, 1)
        self.assertIn("no pass 2: no waiting source left for 07:00, 08:45", said)

    def test_the_passes_stop_when_the_tool_itself_refused(self):
        self.inbox([row(1), row(2)])
        calls, said, n = self.night(picks={1: (1, TOOL_VISIBLE)}, only=["07:00"], passes=True)
        self.assertEqual((n, len(self.intakes(calls))), (1, 1))
        self.assertTrue(any("no pass 2: the tool itself refused (07:00, TOOL)" in ln for ln in said), said)

    def test_a_tool_failure_beside_another_failure_still_passes_again(self):
        self.reload(spread=True)
        rows = [row(i, duration=300) for i in range(1, 5)]                   # short: two slots each
        a, b = rows[0], rows[1]
        self.inbox(rows)
        calls, said, n = self.night(intake={b["url"]: 1}, picks={1: (1, TOOL_VISIBLE)},
                                    only=["07:00", "08:45", "10:30", "12:15"], passes=True)
        self.assertEqual(n, 2, said)               # pass 1: a TOOL and b's intake; pass 2: TOOL alone, so it stops
        self.assertTrue(any(ln.startswith("no pass 3: the tool itself refused") for ln in said), said)
        self.assertEqual(self.intakes(calls)[:2], [a["url"], b["url"]])

    def test_a_source_too_long_for_the_time_left_is_passed_over_for_the_next(self):
        big, small = row(1, duration=1700), row(2, duration=600)             # both under the 30-minute cap
        self.inbox([big, small])
        calls, said, n = self.night(only=["07:00"], passes=True, now=local(self.cfg, DAY, "00:30"))
        self.assertEqual(self.intakes(calls), [small["url"]], said)          # 1700 x 4 from 00:30 ends 02:23
        self.assertEqual(n, 1)
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)

    def test_a_source_made_late_by_the_intakes_before_it_fails_its_slots_and_the_next_pass_moves_on(self):
        self.reload(spread=True)
        a, b, c = row(1, duration=600), row(2, duration=1500), row(3, duration=300)
        self.inbox([a, b, c])
        clock = [local(self.cfg, DAY, "00:00")]                               # b: 00:00 + 100 min = 01:40, fits ...
        calls, said, n = self.night(only=["07:00", "08:45", "10:30", "12:15", "14:00", "15:45", "17:30"],
                                    passes=True, now=None, clock=clock, advance={a["url"]: timedelta(minutes=30)})
        self.assertEqual(self.intakes(calls)[:2], [c["url"], a["url"]], said)   # shortest first; a takes 30 min
        self.assertNotIn(b["url"], self.intakes(calls))                          # ... then b would end 02:10
        self.assertTrue(any("not cut now: 25 min long, its intake would end about 02:10" in ln for ln in said), said)
        failed = [k for k, s in self.board()["slots"].items() if s.get("fail_kind") == "late"]
        self.assertTrue(failed, said)
        for k in failed:
            self.assertEqual(self.board()["slots"][k]["status"], B.FAILED)

    def test_the_reactions_slot_stays_held_across_passes(self):
        a, b = row(1), row(2)
        self.inbox([a, b])
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["19:15"]["format"] = "reactions"
        calls, said, n = self.night(intake={a["url"]: 1}, only=["07:00", "19:15"], passes=True)
        s = self.board()["slots"]["19:15"]
        self.assertEqual((s["status"], s["format"]), (B.OPEN, "reactions"))
        self.assertIn("Held for the day's REACTIONS clip", B.last_note(s))
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.READY)
        self.assertEqual(n, 2)

    def test_other_makers_make_one_pass(self):
        led = self.cfg.account("politics")
        said = []
        with mock.patch.object(M.K, "collect", lambda *a, **k: ["collected"]):
            self.assertEqual(M.make_passes(self.cfg, led, DAY, say=said.append), 1)
        self.assertIn("collected", said)

    def test_the_worker_runs_the_passes_and_a_plain_make_does_not(self):
        import factory as F
        a = argparse.Namespace(account="gaming", date=DAY.isoformat(), slots=None, dry_run=False, standin=False,
                               background=False, _worker=True)
        import signal
        got = []
        handlers = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}   # cmd_make sets its own
        try:
            with mock.patch.object(F.M, "make_passes", lambda cfg, acct, day, only, say: got.append(("passes", only))), \
                    mock.patch.object(F.M, "make", lambda *x, **k: got.append(("make",)) or []), \
                    mock.patch.object(F.K, "collect", lambda *x, **k: []), \
                    mock.patch.object(F.M, "hold_worker_lock", lambda acct: open(os.devnull)):
                with contextlib.redirect_stdout(io.StringIO()):          # its status lines
                    F.cmd_make(self.cfg, a)
                    a._worker = False
                    F.cmd_make(self.cfg, a)
                    a._worker, a.dry_run = True, True
                    F.cmd_make(self.cfg, a)
        finally:
            for s, h in handlers.items():
                signal.signal(s, h)
        self.assertEqual(got, [("passes", None), ("make",), ("make",)])


# ------------------------------------------------------------------------------- review fixes (10-18)
class EveryIntakeFails(Yield):
    """The network, yt-dlp or YouTube's bot check breaks: every intake fails within seconds. No video is blamed, the
    passes stop, and the next run still finds every row."""

    def test_every_intake_failing_blames_no_upload_and_the_retry_still_finds_them(self):
        rows = [row(i, duration=300 + 60 * i) for i in range(1, 6)]           # 10-19's supply: 5 waiting uploads
        self.inbox(rows)
        calls, said, n = self.night(intake={r["url"]: 1 for r in rows}, passes=True)
        self.assertEqual(n, 1, said)
        dealt = self.intakes(calls)
        self.assertEqual(len(dealt), 4)                                       # 10 slots, 3 a source: 4 dealt, once
        self.assertTrue(any(ln.startswith("no pass 2: every intake of pass 1 failed (4); likely the network")
                            for ln in said), said)
        self.assertTrue(any("4 intake failure(s) not held against their upload(s)" in ln for ln in said), said)
        for r in rows:
            self.assertNotIn("intake_failed", self.row_of(r["url"]))
        bd = self.board()
        self.assertEqual(M.tried_on(bd), set())
        self.assertTrue(all(s.get("sources_tried") == [] for s in bd["slots"].values() if s.get("source_url")))
        # the 03:41 gap-fill or the 03:45 retry, the network back: every row is there to cut again
        self.assertEqual(len(M.gta_sources(self.acct, M.used_sources(self.acct) | M.tried_on(bd))), 5)
        calls, said, n = self.night(passes=True)
        self.assertEqual(self.intakes(calls)[:4], dealt, said)
        self.assertEqual({s["status"] for s in self.board()["slots"].values()}, {B.READY})

    def test_a_plain_make_with_every_intake_failing_blames_no_upload(self):
        rows = [row(1), row(2)]
        self.inbox(rows)
        _, lines, _ = self.night(intake={r["url"]: 1 for r in rows}, only=self.FOUR)
        self.assertEqual([("intake_failed" in self.row_of(r["url"])) for r in rows], [False, False])
        self.assertTrue(any("2 intake failure(s) not held" in ln for ln in lines), lines)

    def test_an_outage_from_pass_two_blames_only_pass_one(self):
        rows = [row(i) for i in range(1, 6)]
        a, b, c, d, e = rows
        self.inbox(rows)
        seven = ["07:00", "08:45", "10:30", "12:15", "14:00", "15:45", "17:30"]
        calls, said, n = self.night(intake={a["url"]: 1, c["url"]: 1, d["url"]: 1, e["url"]: 1}, only=seven,
                                    passes=True)
        # pass 1: a (3) fails, b (3) works, c (1) fails; pass 2: d (3) and e (1) both fail: no pass 3
        self.assertEqual((n, self.intakes(calls)), (2, [a["url"], b["url"], c["url"], d["url"], e["url"]]), said)
        self.assertTrue(any(ln.startswith("no pass 3: every intake of pass 2 failed (2)") for ln in said), said)
        self.assertEqual([("intake_failed" in self.row_of(r["url"])) for r in rows], [True, False, True, False, False])
        tried = M.tried_on(self.board())
        self.assertTrue({M.video_id(a["url"]), M.video_id(c["url"])} <= tried)
        self.assertFalse({M.video_id(d["url"]), M.video_id(e["url"])} & tried)

    def test_one_intake_failing_with_the_watchlist_scan_ends_the_passes(self):
        a, b = row(1), row(2)
        self.inbox([a, b])
        calls, said, n = self.night(intake={a["url"]: 1}, only=["07:00"], passes=True, scan_rc=1)
        self.assertEqual((n, self.intakes(calls)), (1, [a["url"]]), said)
        self.assertTrue(any("every intake of pass 1 failed (1) and so did the watchlist scan" in ln for ln in said))
        self.assertNotIn("intake_failed", self.row_of(a["url"]))

    def test_one_intake_failing_in_two_passes_running_ends_the_passes(self):
        rows = [row(i) for i in range(1, 4)]
        self.inbox(rows)
        calls, said, n = self.night(intake={r["url"]: 1 for r in rows}, only=["07:00"], passes=True)
        self.assertEqual((n, len(self.intakes(calls))), (2, 2), said)
        self.assertTrue(any("every intake of pass 2 failed (1), as in an earlier pass" in ln for ln in said), said)
        self.assertFalse(any("intake_failed" in self.row_of(r["url"]) for r in rows))

    def test_a_timeout_always_counts(self):
        a, b = row(1), row(2)
        self.inbox([a, b])
        self.night(intake={a["url"]: "timeout", b["url"]: 1}, only=self.FOUR, passes=True)
        self.assertEqual(self.row_of(a["url"])["intake_failed"]["timeouts"], 1)
        self.assertNotIn("intake_failed", self.row_of(b["url"]))

    def test_used_sources_counts_no_failed_slot_without_a_clip(self):
        u1, u2, u3 = (f"https://www.youtube.com/watch?v=used0000{i:03d}" for i in (1, 2, 3))
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"].update(status=B.FAILED, source_url=u1, sources_tried=[u1])   # nothing cut from it
            bd["slots"]["08:45"].update(status=B.FAILED, source_url=u2)          # a board from before: still counts
            bd["slots"]["10:30"].update(status=B.READY, source_url=u3, sources_tried=[u3], clip={"sha256": "x"})
        used = M.used_sources(self.acct)
        self.assertNotIn(u1, used)
        self.assertTrue({u2, u3} <= used)


class RenderUntil(Yield):
    def test_the_night_starts_no_step_after_render_until(self):
        self.inbox([row(1, duration=300)])
        by = str(int(local(self.cfg, DAY, "02:30").timestamp()))
        calls, said, _ = self.night(only=["07:00"], passes=True, now=local(self.cfg, DAY - timedelta(days=1), "21:00"))
        self.assertEqual(self.env_at_intake, [by])                            # every RL.hold of the run reads it
        self.assertTrue(any(ln.startswith("no intake or render of this run starts after 02:30") for ln in said))
        self.assertNotIn("CLIP_RENDERLOCK_UNTIL", os.environ)                 # taken back after the run

    def test_a_run_with_its_own_deadline_keeps_it(self):
        self.inbox([row(1, duration=300)])
        own = str(int(local(self.cfg, DAY, "04:50").timestamp()))            # the 03:45 retry, the gap-fill
        with mock.patch.dict(os.environ, {"CLIP_RENDERLOCK_UNTIL": own}):
            self.night(only=["07:00"], passes=True, now=local(self.cfg, DAY, "03:45"))
            self.assertEqual(os.environ["CLIP_RENDERLOCK_UNTIL"], own)
        self.assertEqual(self.env_at_intake, [own])

    def test_none_after_it_or_switched_off(self):
        self.inbox([row(1, duration=300)])
        self.night(only=["07:00"], passes=True, now=local(self.cfg, DAY, "03:00"))
        self.assertEqual(self.env_at_intake, [None])                          # past 02:30: no deadline set
        self.fresh()
        self.reload(render_until="")
        self.inbox([row(1, duration=300)])
        self.night(only=["07:00"], passes=True, now=local(self.cfg, DAY - timedelta(days=1), "21:00"))
        self.assertEqual(self.env_at_intake, [None])

    def test_an_intake_that_could_not_end_by_it_is_not_started(self):
        self.inbox([row(1, duration=900)])                                    # x3 = 45 min from 02:00: 02:45
        calls, said, _ = self.night(only=["07:00"], passes=True, now=local(self.cfg, DAY, "02:00"))
        self.assertEqual(self.intakes(calls), [])
        self.assertTrue(any("after this run's start deadline 02:30" in ln for ln in said), said)

    def test_an_intake_the_render_queue_does_not_start_is_not_held_against_it(self):
        a = row(1)
        self.inbox([a])
        past = str(int((datetime.now() - timedelta(minutes=5)).timestamp()))
        with mock.patch.dict(os.environ, {"CLIP_RENDERLOCK_UNTIL": past}):
            calls, lines, _ = self.night(only=["07:00"])
        self.assertEqual(self.intakes(calls), [])
        s = self.board()["slots"]["07:00"]
        self.assertEqual((s["status"], s["fail_kind"], s["sources_tried"]), (B.FAILED, "deadline", []))
        self.assertIn("deadline", B.last_note(s))
        self.assertNotIn("intake_failed", self.row_of(a["url"]))


class LateLeavesWhatCameIn(Yield):
    def test_a_slot_filled_meanwhile_is_left_and_an_older_failure_kept(self):
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"].update(status=B.READY, slug="dropped-in")
            bd["slots"]["10:30"].update(status=B.FAILED, fail_kind="tool", failed_at="2030-01-06T05:00:00.000000+00:00")
        done = M._fail(self.cfg, self.acct, DAY, ["07:00", "08:45", "10:30"], "not cut from X now: late", kind="late",
                       only_if=(B.OPEN, B.FAILED))
        s = self.board()["slots"]
        self.assertEqual(done, ["08:45", "10:30"])
        self.assertEqual((s["07:00"]["status"], s["07:00"].get("fail_kind")), (B.READY, None))
        self.assertEqual((s["08:45"]["status"], s["08:45"]["fail_kind"]), (B.FAILED, "late"))
        self.assertEqual((s["10:30"]["fail_kind"], s["10:30"]["failed_at"]),
                         ("tool", "2030-01-06T05:00:00.000000+00:00"))
        self.assertEqual(B.last_note(s["10:30"]), "not cut from X now: late")


# ------------------------------------------------------------------------------- (6) a refused slot refills
class RefusedSlotIsMadeAgain(Yield):
    def test_the_maker_and_the_retry_take_a_slot_whose_clip_was_refused(self):
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["07:00"].update(status=B.FAILED, fail_kind="refused",
                                        refused_clips=[{"sha256": "ab" * 32, "seconds": 64.2}])
        want, _ = NR.due_slots(self.cfg, self.acct, DAY, local(self.cfg, DAY, "03:45"))
        self.assertIn("07:00", want)
        self.inbox([row(1)])
        self.night(only=["07:00"])
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["status"], B.READY)
        self.assertNotIn("fail_kind", s)


# ------------------------------------------------------------------------------- (5) and (6) through collect
def mp4(path: Path, secs: float, color: str = "red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["nice", "-n", "19", "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={color}:s=108x192:d={secs}", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                    "-t", str(secs), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
                   check=True)
    return path


def clip_dir(root: Path, hhmm: str, slug: str, secs: float, color: str = "red") -> Path:
    d = root / f"{hhmm} {slug}"
    mp4(d / f"01-{slug}.mp4", secs, color)
    for p in ("youtube", "tiktok", "facebook", "reels", "tiktok_reach", "instagram"):
        (d / f"{p}.txt").write_text(f"{slug} words for {p}\n")
    return d


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-gtayield-"))
        self._state = C.STATE
        C.STATE = self.tmp / "state"
        raw = pin_politics(json.loads((HERE.parent / "config" / "accounts.json").read_text()))
        raw["drafts_root"] = str(self.tmp / "drafts")
        for slug, a in raw["accounts"].items():
            a["account_dir"] = str(self.tmp / a["name"])
            a["skill_dir"] = str(self.tmp / f"skill-{slug}")
            a["delivered"] = [str(self.tmp / (a["name"] + " Run {MM}.{DD}.{YYYY}"))]
            a["seconds"] = {"min": 1, "max": 2}
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(raw))
        self.cfg = C.load(p)
        self.gta = self.cfg.account("gaming")
        self.run = self.gta.delivered_dirs(DAY)[0]

    def tearDown(self):
        C.STATE = self._state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_hand_cut_drops_the_plans_picks_and_keeps_its_source_tried(self):
        pj = self.gta.skill_dir / "work" / "factory-b-hazardous" / "project.json"
        pj.parent.mkdir(parents=True)
        pj.write_text(json.dumps({"host": "Hazardous", "source_url": "https://www.youtube.com/watch?v=WT98BmWKNss",
                                  "source_title": "GTA 5 stunts"}))
        sched = self.gta.account_dir / "Publish" / "schedule.json"
        sched.parent.mkdir(parents=True)
        sched.write_text(json.dumps({DAY.isoformat(): {"19:15": {"job": "factory-b-hazardous", "slug": "adder-jump",
                                                                  "template": "gameplay-clean"}}}))
        plan_url = "https://www.youtube.com/watch?v=OCLJLW0e8jo"
        with B.edit(self.cfg, self.gta, DAY) as b:
            b["slots"]["19:15"].update(status=B.FAILED, job="factory-a-mrboss", host="MrBossFTW", source_url=plan_url,
                                       picks_tried=[1, 2, 3], pick=3, pick_span=[300.0, 340.0],
                                       question_written={"question": "old?"}, sources_tried=[plan_url])
        clip_dir(self.run, "1915", "adder-jump", 1.5)
        K.collect(self.cfg, self.gta, DAY)
        s = B.read(self.gta, DAY)["slots"]["19:15"]
        self.assertEqual((s["status"], s["job"], s["host"]), (B.READY, "factory-b-hazardous", "Hazardous"), s["notes"])
        for k in ("picks_tried", "pick", "pick_span", "question_written"):
            self.assertNotIn(k, s)
        self.assertEqual(s["sources_tried"], [plan_url])
        self.assertIn("OCLJLW0e8jo", M.tried_on(B.read(self.gta, DAY)))
        self.assertIn("WT98BmWKNss", M.used_sources(self.gta))

    def test_a_hand_cut_into_a_slot_with_no_history_still_keeps_the_plans_source(self):
        pj = self.gta.skill_dir / "work" / "factory-b" / "project.json"
        pj.parent.mkdir(parents=True)
        pj.write_text(json.dumps({"host": "TGG", "source_url": "https://www.youtube.com/watch?v=Ej4S9MxmmOw"}))
        sched = self.gta.account_dir / "Publish" / "schedule.json"
        sched.parent.mkdir(parents=True)
        sched.write_text(json.dumps({DAY.isoformat(): {"08:45": {"job": "factory-b", "slug": "lucia"}}}))
        with B.edit(self.cfg, self.gta, DAY) as b:        # a board from before sources_tried: only source_url
            b["slots"]["08:45"].update(status=B.FAILED, job="factory-a", source_url="https://youtu.be/rP52QhtFjf8")
        clip_dir(self.run, "0845", "lucia", 1.5)
        K.collect(self.cfg, self.gta, DAY)
        s = B.read(self.gta, DAY)["slots"]["08:45"]
        self.assertEqual(s["sources_tried"], ["https://youtu.be/rP52QhtFjf8"])
        self.assertEqual(s["source_url"], "https://www.youtube.com/watch?v=Ej4S9MxmmOw")

    def test_a_refused_clip_leaves_its_slot_fillable_and_is_never_taken(self):
        d = clip_dir(self.run, "0700", "too-long", 3.0)                   # over the test band's 2 s
        lines = K.collect(self.cfg, self.gta, DAY)
        s = B.read(self.gta, DAY)["slots"]["07:00"]
        self.assertTrue(any("07:00 too-long: REFUSED, 3.0s is over 2s (the slot stays fillable)" in ln
                            for ln in lines), lines)
        self.assertEqual((s["status"], s["fail_kind"]), (B.FAILED, "refused"))
        self.assertIn(s["status"], B.FILLABLE)
        self.assertEqual([r["slug"] for r in s["refused_clips"]], ["too-long"])
        self.assertEqual(s["refused_clips"][0]["sha256"], B.sha256_file(d / "01-too-long.mp4"))
        for k in ("clip", "copy", "folder", "source_folder", "signoff"):
            self.assertNotIn(k, s)
        self.assertTrue(B.last_note(s).startswith("REFUSED 0700 too-long: 3.0s is over the 2s ceiling"))
        self.assertIn("nothing to sign", " ".join(S.approve(self.cfg, self.gta, DAY, ["07:00"], "andrew")))
        # the same clip again, under another slot's time and touched: never taken, in any slot
        again = self.run / "0845 too-long-again"
        again.mkdir()
        shutil.copy2(d / "01-too-long.mp4", again / "01-too-long-again.mp4")
        for p in d.glob("*.txt"):
            shutil.copy2(p, again / p.name)
        lines = K.collect(self.cfg, self.gta, DAY)
        self.assertTrue(any("0845 too-long-again: the clip refused for its length at 07:00; never taken" in ln
                            for ln in lines), lines)
        bd = B.read(self.gta, DAY)["slots"]
        self.assertEqual((bd["07:00"]["status"], bd["08:45"]["status"]), (B.FAILED, B.OPEN))
        # a new clip in band for 07:00 fills it
        shutil.rmtree(d)
        clip_dir(self.run, "0700", "good-one", 1.5, color="green")
        K.collect(self.cfg, self.gta, DAY)
        s = B.read(self.gta, DAY)["slots"]["07:00"]
        self.assertEqual((s["status"], s["slug"]), (B.READY, "good-one"))
        self.assertNotIn("fail_kind", s)
        self.assertEqual(len(s["refused_clips"]), 1)                     # the record stays: still never taken

    def test_collects_own_failures_carry_fail_kind(self):
        bad = self.run / "0700 unreadable"
        bad.mkdir(parents=True)
        (bad / "01-unreadable.mp4").write_bytes(b"not a video")
        for p in ("youtube", "tiktok", "facebook", "reels", "tiktok_reach", "instagram"):
            (bad / f"{p}.txt").write_text("words\n")
        d = clip_dir(self.run, "0845", "no-words", 1.5)
        for f in d.glob("*.txt"):
            f.unlink()
        K.collect(self.cfg, self.gta, DAY)
        bd = B.read(self.gta, DAY)["slots"]
        for k in ("07:00", "08:45"):
            self.assertEqual((bd[k]["status"], bd[k]["fail_kind"]), (B.FAILED, "collect"), bd[k].get("notes"))
            self.assertRegex(bd[k]["failed_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00$")

    def test_the_politics_fills_a_refused_slot_with_its_next_clip(self):
        led = self.cfg.account("politics")
        run = led.delivered_dirs(DAY)[0]
        clip_dir(run, "0700", "too-short", 0.5)
        K.collect(self.cfg, led, DAY)
        self.assertEqual(B.read(led, DAY)["slots"]["07:00"]["status"], B.FAILED)
        clip_dir(run, "0700", "second-try", 1.5, color="blue")
        K.collect(self.cfg, led, DAY)
        s = B.read(led, DAY)["slots"]["07:00"]
        self.assertEqual((s["status"], s["slug"]), (B.READY, "second-try"))


if __name__ == "__main__":
    unittest.main()
