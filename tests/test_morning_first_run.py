import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
# The morning report's first real run (2026-10-21) and what it got wrong: the file said nothing to sign while the
# coach's 2 proposals were open (learn propose now writes it again); a problem line had no day, so the coach put the
# report day's failed slots on the clips' day (each line now says its day); a backfill took 15:51 snapshots as the
# 24-hour point of posts that were 22 hours old (a label whose time has not come gets no point, an on-time live read
# replaces a snapshot's point, a read over an hour early is marked early and kept out of every usual, and --redo takes
# an early point again from an on-time snapshot); a clip YouTube held back was the Politics's Worst (a clip is ranked on
# the platforms that took it, the held-back post shown beside it). Also: titles cut at a word, the coach's proposals
# say whose evidence they are, and the transcript the coach reads gives each cue's own start. Throwaway folders.

import contextlib
import io
import json
import sys
import unittest
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_checkpoints as TC                # noqa: E402  (fixtures only: a module, so its tests are not run twice)
import test_learn_morning as TM              # noqa: E402

from clipfactory import checkpoints as CP    # noqa: E402
from clipfactory import config as C          # noqa: E402
from clipfactory import learn as L           # noqa: E402
from clipfactory import stats as ST          # noqa: E402
from clipfactory import store                # noqa: E402

D = TM.D
D1 = D - timedelta(days=1)


def say(cfg, argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        L.cli(cfg, argv)
    return out.getvalue()


# ================================================================================================= the morning file
class ProposeWritesTheFileTest(TM.Morning):
    def test_propose_counts_in_waiting_for_you(self):
        d = self.desk_on()
        self.sixty()
        L.daily(self.cfg, now=self.now)
        f = d / f"Clip report {D + timedelta(days=1):%m.%d.%Y}.md"
        self.assertIn("- Waiting for you: nothing to sign.", f.read_text())
        notes = self.tmp / "notes.json"
        notes.write_text(json.dumps({"summary": "Open on the answer."}))
        L.write_notes(self.cfg, self.gta, D, notes, "clip-results-coach")       # 05:09:33 on 10.21: notes first
        props = self.tmp / "props.json"
        props.write_text(json.dumps([{"kind": "advice", "words": "Check the 07.13 17:30 Facebook post in Business Suite."}]))
        said = say(self.cfg, ["propose", "gaming", "--file", str(props), "--by", "clip-results-coach",
                              "--date", D.isoformat()])                       # 05:09:42: then the proposals
        self.assertIn("1 proposal added", said)
        self.assertIn(str(f), said)
        text = f.read_text()
        self.assertIn("- Waiting for you: 1 change to sign on Clip Review, Reports.", text)
        self.assertIn("- Coach: Open on the answer.", text)                    # the notes are still there
        self.assertIn("1 open: approve or decline", L.report_path(self.gta, D, "md").read_text())
        p = next(iter(L.proposals(self.gta).values()))
        self.assertEqual(p["evidence"], {"metric": "results coach, clips of 07.13", "n": 0, "day": D.isoformat()})
        from clipfactory import reportspage as RP
        self.assertEqual(RP._evidence_words(p), "results coach, clips of 07.13")

    def test_the_weekly_task_keeps_its_own_label(self):
        self.sixty()
        L.daily(self.cfg, now=self.now)
        props = self.tmp / "props.json"
        props.write_text(json.dumps([{"kind": "advice", "words": "Read the week's held-back posts."}]))
        made = L.propose_file(self.cfg, self.gta, props, "clip-weekly-report", day=D)
        self.assertEqual(L.proposals(self.gta)[made[0]]["evidence"], {"metric": "weekly report", "n": 0})

    def test_nothing_new_nothing_written(self):
        d = self.desk_on()
        self.sixty()
        L.daily(self.cfg, now=self.now)
        props = self.tmp / "props.json"
        props.write_text(json.dumps([{"kind": "advice", "words": "Check the held-back posts."}]))
        say(self.cfg, ["propose", "gaming", "--file", str(props), "--by", "clip-results-coach", "--date", str(D)])
        f = d / f"Clip report {D + timedelta(days=1):%m.%d.%Y}.md"
        f.write_text("kept")
        said = say(self.cfg, ["propose", "gaming", "--file", str(props), "--by", "clip-results-coach",
                              "--date", str(D)])
        self.assertIn("0 proposals added (the same ones are open or declined)", said)
        self.assertEqual(f.read_text(), "kept")


class DatedProblemsTest(TM.Morning):
    def test_each_problem_says_its_day(self):
        d = self.desk_on()
        self.sixty()
        self.extra_clip(slot="21:00", yt=2, fb=1, ig=3)          # slow starts: posts of the day before
        L.daily(self.cfg, now=self.now)
        text = (d / f"Clip report {D + timedelta(days=1):%m.%d.%Y}.md").read_text()
        minute = text[text.index("## In one minute"):text.index("---")]
        self.assertIn("- Biggest problem: 2 slots failed to make on 07.14: about", minute)
        probs = text[text.index("## Biggest problems"):]
        self.assertIn("1. 2 slots failed to make on 07.14: about", probs)
        self.assertIn("posts started at under half the usual (posts of 07.13): about", probs)
        self.assertIn("1 clip could not post to Instagram on 07.14: no Instagram cut: about", probs)
        self.assertIn("1 post went up late (up to 3.0 hours) on 07.14: about", probs)
        self.assertIn("1 slot had no clip by its time on 07.14: about", probs)
        self.assertIn("Yesterday (07.14): slots planned", text)
        self.assertIn("Clips that went up 07.13", text)

    def test_words_of_each_kind(self):
        rep = {"day": "2031-07-14", "window": {"from": "2031-07-13", "to": "2031-07-14"}}
        self.assertEqual(L.dated_words({"kind": "gap", "words": "YouTube is the weakest platform: 13 views"}, rep),
                         "YouTube is the weakest platform: 13 views (the usuals at the end of 07.14)")
        drop = "Facebook views dropped: the last 5 posts (07.13) got 25"
        self.assertEqual(L.dated_words({"kind": "drop", "words": drop}, rep), drop)        # it names its own days
        weak = {"kind": "weak", "words": "The gaggle format on Facebook: 0.5x the usual", "n": 12,
                "evidence": [{"board": "2031-07-13"}] * 10}                               # more than it lists
        self.assertEqual(L.dated_words(weak, rep), weak["words"] + " (posts of 07.13 to 07.14)")
        self.assertEqual(L.dated_words({"kind": "slot", "words": "x"}, {}), "x")           # no day: as it was

    def test_the_coach_reads_the_days(self):
        self.sixty()
        L.daily(self.cfg, now=self.now)
        out = "\n".join(L.clips_text(self.cfg, self.gta, D))
        self.assertIn("Report day 07.14: its problems below belong to 07.14 unless a line names other days. The clips "
                      "ranked went up 07.13", out)
        self.assertIn("Problems (report day 07.14):\n  1. 2 slots failed to make on 07.14: about", out)
        text = (ROOT / "prompts" / "results-coach.md").read_text()
        self.assertIn("Two days, never mixed", text)
        self.assertIn("belong to the report day", text)
        self.assertIn("(10.19 had 7; the 8 were 10.20's)", text)

    def test_the_full_report_keeps_its_words(self):
        self.sixty()
        L.daily(self.cfg, now=self.now)
        md = L.report_path(self.gta, D, "md").read_text()                # titled with its day; lines as before
        self.assertIn("1. 2 slots failed to make: about", md)
        rep = json.loads(L.report_path(self.gta, D).read_text())
        self.assertEqual(rep["problems"][0]["words"], "2 slots failed to make")   # stored words stay undated (week)


# ================================================================================================= held back
class HeldBackBesideTest(TM.Morning):
    def test_ranked_and_picked_on_the_platforms_that_took_it(self):
        self.sixty()
        self.extra_clip(slot="17:30", yt=1, fb=240, ig=170)       # YouTube held it back; Facebook and Instagram at 1x
        self.extra_clip(slot="21:00", yt=60, fb=120, ig=90)       # every platform at about half: the weakest clip
        rep = L.report(self.cfg, self.gta, day=D, now=self.now, propose=False)
        by = {c["slot"]: c for c in rep["clips"]}
        held = by["17:30"]
        self.assertEqual(held["held_back"], ["youtube_shorts"])
        self.assertGreater(held["x"], 0.9)                       # not 0.7x with YouTube's 1 inside it
        self.assertEqual(rep["clips"][-1]["slot"], "21:00")      # the Worst is the weak clip, not the held one
        self.assertEqual(held["views_24h"], 1 + 240 + 170)       # every view still counted in the total
        lines = L.minute_lines(rep)
        worst = next(ln for ln in lines if ln.startswith("- Worst:"))
        self.assertIn("21:00", worst)
        self.assertTrue(lines[0].endswith("the usual on the platforms that took them."), lines[0])
        text = "\n".join(L.clip_lines(rep))
        self.assertIn("   Facebook 240 (1.0x); Instagram 170 (0.9x).\n   Held back, not in its x: YouTube 1 (the usual "
                      "is 120).", text)

    def test_a_best_or_worst_with_a_held_post_names_it_beside(self):
        clips = [{"board": "2031-07-13", "slot": "07:00", "title": "A", "views_24h": 400, "posts": [
                     {"outlet": "youtube_shorts", "views_24h": 8, "baseline_24h": 1258.0, "held_back": True},
                     {"outlet": "facebook_reels", "views_24h": 257, "baseline_24h": 285.0},
                     {"outlet": "instagram_reels", "views_24h": 135, "baseline_24h": None}]},
                 {"board": "2031-07-13", "slot": "10:30", "title": "B", "views_24h": 600, "posts": [
                     {"outlet": "facebook_reels", "views_24h": 600, "baseline_24h": 300.0}]},
                 {"board": "2031-07-13", "slot": "12:15", "title": "C", "views_24h": 100, "posts": [
                     {"outlet": "facebook_reels", "views_24h": 100, "baseline_24h": 300.0}]}]
        rep = {"name": "Politics", "day": "2031-07-14", "clips": clips}   # an old report's x is not read
        got = L.clips_of(rep)
        self.assertEqual([c["slot"] for c in got], ["10:30", "07:00", "12:15"])
        self.assertEqual(got[1]["x"], round(257 / 285, 2))
        lines = L.minute_lines(rep)
        self.assertIn("1.1x the usual on the platforms that took them.", lines[0])     # (600+257+100)/(300+285+300)
        self.assertEqual(next(ln for ln in lines if ln.startswith("- Worst:")),
                         "- Worst: \"C\" 0.3x: Facebook 100.")
        rep["clips"] = clips[:1] + clips[2:]
        best = next(ln for ln in L.minute_lines(rep) if ln.startswith("- Best:"))
        self.assertEqual(best, "- Best: \"A\" 0.9x: Facebook 257, Instagram 135; YouTube held it back (8, the usual "
                               "1,258).")


class WordsTest(unittest.TestCase):
    def test_titles_cut_at_a_word(self):
        t = "Trump on the ceasefire both sides deny: they agreed because I said they agreed"
        got = L._clip_name({"title": t}, 70)
        self.assertEqual(got, "\"Trump on the ceasefire both sides deny: they agreed because I said...\"")
        self.assertLessEqual(len(got) - 2, 70)
        self.assertEqual(L.cut_words("short title", 70), "short title")
        self.assertEqual(L.cut_words("Supercalifragilisticexpialidocious", 10), "Superca...")
        self.assertEqual(L.cut_words("one two, three", 12), "one two...")


# ================================================================================================= checkpoints
class EarlyReadsTest(TC.Base):
    def snaps(self, sid, went, pts):
        hist = C.STATE / "stats" / "history"
        hist.mkdir(parents=True, exist_ok=True)
        reads = [{"at": (went + timedelta(minutes=m)).astimezone(timezone.utc).isoformat(),
                  "posts": {sid: [v, 1, 0, 0, int(went.timestamp())]}} for m, v in pts]
        f = hist / f"{TC.DAY}.json"
        old = json.loads(f.read_text())["reads"] if f.exists() else []
        f.write_text(json.dumps({"day": TC.DAY.isoformat(), "reads": old + reads}))

    def yt_post(self, slot="07:00"):
        ids = self.put(self.gta, TC.DAY, slot, outlets=("youtube_shorts",))
        pid = ids["youtube_shorts"]
        sid = ST.post_id({"account": "gaming", "platform": "youtube", "pid": pid})
        return pid, sid, self.went(TC.DAY, slot)

    def test_1_a_backfill_never_fills_a_label_whose_time_has_not_come(self):
        pid, sid, went = self.yt_post()
        self.snaps(sid, went, [(62, 30), (1341, 4)])                  # 10.19 17:30 Politics: 4 views at 22.4 hours
        self.on(self.gta)
        CP.backfill(self.cfg, now=went + timedelta(hours=23, minutes=19))      # the 16:49 backfill, 40 min before 24h
        e = self.entry(self.gta, "youtube_shorts", pid)
        self.assertEqual([p["label"] for p in e["points"]], ["1h"])
        self.assertNotIn("24h", e["missed"])
        self.yt.on("GET", r"/videos$", TC.yt_answer(views=379))       # released by its real 24 hours
        self.run_at(CP.due_at(went, pid, "24h") + timedelta(minutes=1))
        e = self.entry(self.gta, "youtube_shorts", pid)
        p24 = CP._point(e, "24h")
        self.assertEqual((p24["views"], p24["src"]), (379, "data"))
        self.assertTrue(CP.on_time(p24))

    def test_2_an_on_time_live_read_replaces_a_snapshot_point(self):
        pid, sid, went = self.yt_post()
        due = CP.due_at(went, pid, "24h")
        self.snaps(sid, went, [((due - went).total_seconds() / 60 - 20, 500)])
        self.on(self.gta)
        CP.backfill(self.cfg, now=due + timedelta(minutes=5))
        self.assertEqual(CP._point(self.entry(self.gta, "youtube_shorts", pid), "24h")["src"], "history")
        self.yt.on("GET", r"/videos$", TC.yt_answer(views=640))
        self.run_at(due + timedelta(minutes=10))
        e = self.entry(self.gta, "youtube_shorts", pid)
        self.assertEqual([(p["label"], p["views"], p["src"]) for p in e["points"]], [("24h", 640, "data")])
        self.assertEqual(len(self.yt.calls), 1)
        self.run_at(due + timedelta(minutes=25))                      # read: never asked again
        self.assertEqual(len(self.yt.calls), 1)

    def test_3_a_snapshot_point_past_its_window_stays(self):
        pid, sid, went = self.yt_post()
        self.snaps(sid, went, [(1500, 400)])
        self.on(self.gta)
        CP.backfill(self.cfg, now=went + timedelta(hours=50))
        self.yt.on("GET", r"/videos$", TC.yt_answer(views=900))
        self.run_at(went + timedelta(hours=50))                       # 24h + 26 hours: a live read would be late
        self.assertEqual(self.yt.calls, [])

    def test_4_live_failures_keep_the_snapshot_point(self):
        pid, sid, went = self.yt_post()
        due = CP.due_at(went, pid, "24h")
        self.snaps(sid, went, [((due - went).total_seconds() / 60 - 30, 500)])
        self.on(self.gta)
        CP.backfill(self.cfg, now=due + timedelta(minutes=1))
        self.yt.on("GET", r"/videos$", TC.yt_answer(private=(pid,)))   # not public: a failed read each tick
        for k in range(CP.GIVE_UP + 3):
            self.run_at(due + timedelta(minutes=15 * (k + 1)))
        self.assertEqual(len(self.yt.calls), CP.GIVE_UP)
        e = self.entry(self.gta, "youtube_shorts", pid)
        p24 = CP._point(e, "24h")
        self.assertEqual((p24["views"], p24["src"], p24.get("live_failed")), (500, "history", True))
        self.assertNotIn("24h", e["missed"])

    def test_5_an_early_point_is_marked_and_kept_out_of_the_usual(self):
        went = TC.datetime(2031, 1, 1, tzinfo=timezone.utc)
        reads = [(went + timedelta(minutes=m), [m, 0, 0, 0, 0]) for m in (1300, 1650)]
        pts = CP.history_points(went, reads)
        self.assertEqual(pts["24h"]["views"], 1650)                   # on time first, though 1300 is closer
        self.assertNotIn("early", pts["24h"])
        only = CP.history_points(went, reads[:1])
        self.assertTrue(only["24h"]["early"])                         # the only one near: kept, marked early
        self.assertFalse(CP.on_time(only["24h"]))
        self.assertTrue(CP._is_early({"label": "24h", "age_min": 1341}))          # files written before 10.21 too
        self.assertFalse(CP._is_early({"label": "24h", "age_min": 1390}))         # 50 minutes: on time
        self.assertFalse(CP._is_early({"label": "1h", "age_min": 45}))
        # the usual: the median of on-time points only
        folder = C.STATE / "stats" / "series"
        folder.mkdir(parents=True)
        rows = {f"p{i}": {"went": (went + timedelta(days=i)).isoformat(), "board": "2031-01-01", "slot": "07:00",
                          "points": [{"label": "24h", "views": 100, "age_min": 1500, "src": "history"}]}
                for i in range(8)}
        rows.update({f"e{i}": {"went": (went + timedelta(days=9 + i)).isoformat(), "board": "2031-01-01",
                               "slot": "07:00", "points": [{"label": "24h", "views": 4, "age_min": 1341,
                                                            "src": "history"}]} for i in range(8)})
        (folder / "gaming__youtube_shorts__2031-01.json").write_text(json.dumps(rows))
        CP._CACHE.clear()
        self.assertEqual(CP.baseline_n("gaming", "youtube_shorts", "24h", went + timedelta(days=30)), (100.0, 8))
        line = CP.card_line(self.gta, "youtube_shorts", "e0", went + timedelta(days=30))
        self.assertIn("24h (read at 22.4 h)", line)
        self.assertNotIn("your usual", line)
        self.assertIn("read early (never compared with the usual)", "\n".join(CP._show(self.cfg, "gaming", "e0",
                                                                                         "youtube_shorts")))

    def test_6_redo_takes_an_early_point_again_from_an_on_time_snapshot(self):
        pid, sid, went = self.yt_post()
        fb = self.put(self.gta, TC.DAY, "08:00", outlets=("facebook_reels",))["facebook_reels"]
        folder = C.STATE / "stats" / "series"
        folder.mkdir(parents=True)
        ym = f"{went.astimezone(timezone.utc):%Y-%m}"
        early = {"label": "24h", "views": 4, "age_min": 1341, "src": "history", "at": "x", "extra": {}}
        (folder / f"gaming__youtube_shorts__{ym}.json").write_text(json.dumps(
            {pid: {"went": went.astimezone(timezone.utc).isoformat(), "points": [dict(early)], "missed": [],
                   "backfill": True}}))
        live = dict(early, src="insights")                            # a live read: never touched, even if early
        (folder / f"gaming__facebook_reels__{ym}.json").write_text(json.dumps(
            {fb: {"went": self.went(TC.DAY, "08:00").astimezone(timezone.utc).isoformat(), "points": [live],
                  "missed": []}}))
        self.snaps(sid, went, [(1341, 4), (1480, 455), (1571, 610)])
        self.on(self.gta)
        lines = CP.backfill(self.cfg, now=went + timedelta(days=2))
        self.assertEqual(CP._point(self.entry(self.gta, "youtube_shorts", pid), "24h")["views"], 4)   # not without redo
        lines = CP.backfill(self.cfg, now=went + timedelta(days=2), redo=True)
        p24 = CP._point(self.entry(self.gta, "youtube_shorts", pid), "24h")
        self.assertEqual((p24["views"], p24["age_min"], p24["src"]), (455, 1480, "history"))
        self.assertTrue(CP.on_time(p24))
        self.assertIn("Gaming: 1 early point taken again from an on-time snapshot; 0 still early", "\n".join(lines))
        self.assertEqual(CP._point(self.entry(self.gta, "facebook_reels", fb), "24h")["views"], 4)

    def test_7_the_series_route_marks_an_early_read(self):
        pid, sid, went = self.yt_post()
        self.snaps(sid, went, [(1341, 4)])
        self.on(self.gta)
        CP.backfill(self.cfg, now=went + timedelta(days=2))
        self.clock.set(went + timedelta(days=2))
        code, _, body = CP.get_series(self.cfg, {"acct": "gaming", "outlet": "youtube_shorts", "pid": pid})
        d = json.loads(body)
        self.assertEqual((code, d["points"][0]["label"], d["points"][0].get("early")), (200, "24h", True))
        src = (ROOT / "clipfactory" / "statspage.py").read_text()       # the Stats tab draws it off the usual's line
        self.assertIn("const ontime = pts.filter(z => !z.late && !z.early);", src)
        self.assertIn("z.early ? ' (early)'", src)


class LearnerSkipsAnEarlyPointTest(TM.Morning):
    def test_an_early_series_point_is_not_the_24_hour_number(self):
        self.sixty()
        rows = {(r["board"], r["slot"], r["outlet"]): r for r in L.rows_for(self.cfg, self.gta, self.now)}
        r = rows[(str(D1), "07:00", "youtube_shorts")]
        self.assertEqual(r["v24_src"], "series")
        p = store.path("stats", "series", f"gaming__youtube_shorts__{D1:%Y-%m}.json")
        with store.edit(p, dict) as ser:
            ser[r["pid"]]["points"] = [{"label": "24h", "views": 4, "age_min": 1341, "src": "history"}]
        rows = {(x["board"], x["slot"], x["outlet"]): x for x in L.rows_for(self.cfg, self.gta, self.now)}
        r = rows[(str(D1), "07:00", "youtube_shorts")]
        self.assertNotEqual(r["v24_src"], "series")                   # the snapshots stand in, marked an estimate
        self.assertNotEqual(r["views"]["24h"], 4)


class CluesTest(TM.Morning):
    def test_learn_clips_gives_each_cue_its_own_start(self):
        self.sixty()
        L.daily(self.cfg, now=self.now)
        from clipfactory import coach as CO
        srt = self.tmp / "clip.srt"
        srt.write_text("1\n00:00:00,200 --> 00:00:01,000\nThough the\n\n2\n00:00:23,900 --> 00:00:24,500\nlands here\n")
        orig = CO._srt_of
        CO._srt_of = lambda s: srt
        self.addCleanup(setattr, CO, "_srt_of", orig)
        out = "\n".join(L.clips_text(self.cfg, self.gta, D))
        self.assertIn("transcript (each line at its cue's own start):\n     [0:00] Though the\n     [0:23] lands here",
                      out)


if __name__ == "__main__":
    unittest.main()
