import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""The window intake (2026-10-19), offline: gta_maker "windows" (an owner call, off by default) takes a long VOD in
as the 1-3 windows the skill's vodwindow.py picks, not whole. Pinned here: the switch is off for every account in the
live file (Gaming unchanged); a windowed source counts as its windows in the time guards and the deal; the pick
runs before the intake queues and its windows go to run.py; an off-topic VOD (its chapters are another game) is
turned down for good and a pick with no signal is an ordinary failed intake; a pick already in the job is reused.
run.py and vodwindow.py are faked; nothing reaches Twitch or YouTube."""
import json
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from clipfactory import board as B      # noqa: E402
from clipfactory import makers as M     # noqa: E402
from test_gta_maker_fixes import Base, DAY, cand, row   # noqa: E402

VOD = "https://www.twitch.tv/videos/2902449425"


def vod(n: int = 1, hours: float = 11.8, **kw) -> dict:
    return row(n, kind="rp", duration=int(hours * 3600) if hours else None,
               **dict({"url": VOD if n == 1 else f"https://www.twitch.tv/videos/29000000{n:02d}",
                       "platform": "twitch", "title": "Lang Buddha | NoPixel V"}, **kw))


class Settings(Base):
    def test_the_switch_is_off_for_every_account_in_the_live_file(self):
        self.assertFalse(M.GTA_MAKER_DEFAULTS["windows"])
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        for slug, a in raw["accounts"].items():
            self.assertFalse((a.get("gta_maker") or {}).get("windows"), f"{slug} has windows on in the live file")

    def test_windowed_only_with_the_switch_and_only_long(self):
        st = M.gta_settings(self.acct)
        self.assertFalse(M.windowed(vod(), st))                      # off: as before
        self.reload(windows=True)
        st = M.gta_settings(self.acct)
        self.assertTrue(M.windowed(vod(), st))
        self.assertFalse(M.windowed(row(2, duration=1800), st))      # a 30-minute upload is still taken whole
        self.assertTrue(M.windowed(vod(hours=0), st))                # unknown length counts as unknown_source_s
        self.assertEqual(M.intake_secs(vod(), st), 2400)
        self.assertEqual(M.intake_secs(row(2, duration=1800), st), 1800)
        self.assertEqual(M.intake_secs(vod(hours=0.8), st), 2400)    # 48 min: windowed, never more than itself
        self.assertEqual(M.intake_secs(vod(hours=0.7), st), 2400 if 0.7 * 3600 > 2700 else 2520)

    def test_the_night_guard_counts_the_windows_not_the_vod(self):
        late = self.cfg.slot_time(DAY, "21:00") - timedelta(days=1) + timedelta(hours=1)    # 22:00 the night before
        why = M.intake_too_late(self.cfg, self.acct, DAY, vod(), now=late)
        self.assertIn("after 05:30 (night_until)", why)              # off: an 11.8 h VOD never starts
        self.reload(windows=True)
        self.assertEqual(M.intake_too_late(self.cfg, self.acct, DAY, vod(), now=late), "")
        three = self.cfg.slot_time(DAY, "03:00")
        why = M.intake_too_late(self.cfg, self.acct, DAY, vod(), now=three)
        self.assertIn("11.8 h VOD taken in as 40 min of windows", why)
        # the pick done, its ten minutes are not counted again when the queue's turn comes
        edge = self.cfg.slot_time(DAY, "05:30") - timedelta(seconds=2400 * 4 + 300)
        self.assertNotEqual(M.intake_too_late(self.cfg, self.acct, DAY, vod(), now=edge), "")
        self.assertEqual(M.intake_too_late(self.cfg, self.acct, DAY, vod(), now=edge, picked=True), "")

    def test_the_deal_orders_a_windowed_vod_by_its_windows(self):
        self.reload(windows=True, window_total_s=1500)
        st = M.gta_settings(self.acct)
        whole = row(2, duration=2400)                                 # 40 min, taken whole (under window_from_s)
        order = M.by_length([vod(), whole], st)
        self.assertEqual([i["url"] for i in order], [VOD, whole["url"]])  # 25 min of windows before a 40-min upload
        self.assertLess(M.shortest_first(vod(), st), M.shortest_first(whole, st))


class Making(Base):
    def make_windows(self, pick: dict, moments=None, picks=None, only=("07:00",), already: bool = False):
        """make_gta with run.py and vodwindow.py faked: the pick writes `pick` into the job's windows.json."""
        calls = []

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            if cmd[1] != "watch.py":                     # the scan first, as every make does
                calls.append(list(cmd))
            rc = 0
            if cmd[1] == "vodwindow.py":
                out = next(a.split("=", 1)[1] for a in cmd if a.startswith("--json="))
                Path(out).write_text(json.dumps(dict(pick, url=cmd[3])))
                rc = 0 if pick.get("windows") else 3
            elif cmd[1] == "run.py" and "--job" in cmd:
                job = cmd[cmd.index("--job") + 1]
                d = Path(self.acct.skill_dir) / "work" / job
                d.mkdir(parents=True, exist_ok=True)
                if moments is not None:
                    (d / "moments.json").write_text(json.dumps({"seconds": moments[0], "candidates": moments[1]}))
            elif cmd[1] == "run.py" and "--pick" in cmd:
                rc = (picks or {}).get(int(cmd[cmd.index("--pick") + 1]), (0, ""))[0]
            with open(log, "a") as f:
                f.write(f"\n$ {' '.join(cmd)}\n")
            return subprocess.CompletedProcess(cmd, rc)

        def fake_collect(cfg, acct, day, extra=None, only=None):
            if only:
                with B.edit(cfg, acct, day) as bd:
                    for k in only:
                        if bd["slots"][k]["status"] in B.FILLABLE:
                            bd["slots"][k]["status"] = B.READY
            return []

        with B.edit(self.cfg, self.acct, DAY):
            pass
        if already:
            fmt = self.board()["slots"][only[0]]["format"]
            job = M.job_name(vod(), DAY, M.gta_template(self.acct, fmt), self.acct.job_tag)
            d = Path(self.acct.skill_dir) / "work" / job
            d.mkdir(parents=True, exist_ok=True)
            (d / "windows.json").write_text(json.dumps(dict(pick, url=VOD)))
        with mock.patch.object(M, "run_logged", fake_run), \
                mock.patch.object(M, "pack_gta_slot", lambda *a, **k: "  packed"), \
                mock.patch.object(M.K, "collect", fake_collect):
            lines = M.make_gta(self.cfg, self.acct, DAY, only=list(only))
        return calls, lines

    PICKED = {"windows": [{"start": 18600, "end": 19500, "score": 0.48, "why": "4 viewer clip(s)"},
                          {"start": 21540, "end": 23040, "score": 0.39, "why": "3 viewer clip(s)"}]}

    def test_the_pick_runs_first_and_its_windows_reach_run_py(self):
        self.reload(windows=True)
        self.inbox([vod()])
        calls, lines = self.make_windows(self.PICKED, moments=(2400, [cand(1, 100, 140), cand(2, 900, 940)]))
        kinds = [c[1] for c in calls]
        self.assertEqual(kinds[:2], ["vodwindow.py", "run.py"], calls)
        intake = calls[1]
        self.assertIn("--windows", intake)
        self.assertEqual(intake[intake.index("--windows") + 1], "auto")
        self.assertEqual(intake[intake.index("--window-total") + 1], "40")
        self.assertEqual(intake[intake.index("--window-len") + 1], "25")
        self.assertTrue(any(a == "--json=" + str(Path(self.acct.skill_dir) / "work" / intake[intake.index("--job") + 1]
                                                  / "windows.json") for a in calls[0]), calls[0])
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["status"], B.READY, lines)
        self.assertTrue(any("windows: 2 of the VOD, 40 min (5:10:00-5:25:00, 5:59:00-6:24:00)" in n
                            for n in s.get("notes") or []), s.get("notes"))

    def test_off_the_switch_is_exactly_as_before(self):
        self.inbox([row(1, duration=900)])
        calls, _ = self.make_windows(self.PICKED, moments=(900, [cand(1, 100, 140)]))
        self.assertNotIn("vodwindow.py", [c[1] for c in calls])
        self.assertFalse(any("--windows" in c for c in calls))

    def test_a_short_source_is_taken_whole_with_the_switch_on(self):
        self.reload(windows=True)
        self.inbox([row(1, duration=1500)])
        calls, _ = self.make_windows(self.PICKED, moments=(1500, [cand(1, 100, 140)]))
        self.assertNotIn("vodwindow.py", [c[1] for c in calls])
        self.assertFalse(any("--windows" in c for c in calls))

    def test_a_vod_of_another_game_is_turned_down_for_good(self):
        self.reload(windows=True)
        p = self.inbox([vod()])
        off = {"windows": [], "why_none": "no chapter of this VOD is on topic (Just Chatting, Valorant)"}
        calls, lines = self.make_windows(off)
        self.assertEqual([c[1] for c in calls], ["vodwindow.py"])     # nothing downloaded
        s = self.board()["slots"]["07:00"]
        self.assertEqual(s["status"], B.FAILED)
        self.assertTrue(any("no chapter of this VOD is on topic" in n for n in s.get("notes") or []), s)
        r = json.loads(p.read_text())["items"]["2902449425"]
        self.assertEqual(r["status"], "off-topic")
        self.assertIn("Just Chatting", r["off_topic_why"])
        self.assertTrue(any("marked off-topic" in ln for ln in lines), lines)

    def test_no_signal_is_a_plain_failed_intake_not_held_alone(self):
        self.reload(windows=True)
        p = self.inbox([vod()])
        calls, lines = self.make_windows({"windows": [], "why_none": "no signal answered"})
        self.assertEqual([c[1] for c in calls], ["vodwindow.py"])
        self.assertEqual(self.board()["slots"]["07:00"]["status"], B.FAILED)
        r = json.loads(p.read_text())["items"]["2902449425"]
        self.assertEqual(r["status"], "new")                          # a network night blames no video
        self.assertNotIn("intake_failed", r)

    def test_the_harvest_follows_the_lanes(self):
        # Gaming has a b-roll lane: its intake harvests as before (no flag)
        self.inbox([row(1, duration=900)])
        calls, _ = self.make_windows(self.PICKED, moments=(900, [cand(1, 100, 140)]))
        self.assertFalse(any("--no-harvest" in c for c in calls if c[1] == "run.py"))
        self.assertTrue(M.harvest_on(self.acct))
        # a gameplay-only account (Stunts, Roleplay) never reads the shots: auto skips the harvest
        with mock.patch.object(M, "_lanes", lambda acct: {"gameplay"}):
            self.assertFalse(M.harvest_on(self.acct))
        self.reload(harvest=False)
        self.assertFalse(M.harvest_on(self.acct))
        self.fresh()
        self.reload(harvest=False, windows=True)
        self.inbox([vod()])
        calls, _ = self.make_windows(self.PICKED, moments=(2400, [cand(1, 100, 140)]))
        intake = next(c for c in calls if c[1] == "run.py" and "--job" in c)
        self.assertIn("--no-harvest", intake)
        self.assertIn("--windows", intake)

    def test_a_pick_already_in_the_job_is_reused(self):
        self.reload(windows=True)
        self.inbox([vod()])
        calls, _ = self.make_windows(self.PICKED, moments=(2400, [cand(1, 100, 140)]), already=True)
        self.assertEqual([c[1] for c in calls][:1], ["run.py"])
        self.assertIn("--windows", calls[0])


if __name__ == "__main__":
    unittest.main()
