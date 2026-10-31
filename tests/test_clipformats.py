import os, tempfile; os.environ["CLIP_FACTORY_OFFLINE"] = "1"; os.environ.setdefault("CLIP_FACTORY_STATE", tempfile.mkdtemp())  # noqa: E401,E702
"""Stunts' formats (clipformats.py, 2026-10-29, Andrew locked the board: STUNT, CHASE, AIR STUNT, ONE IN A
MILLION, FAST DRIVE, DRIVE TIME; PARKOUR and HEIST GETAWAY from the launch; one video gives one clip a day), offline:
a new board writes each slot's moment; a source fits a moment by its title and its creator; the night deals each
slot the source that fits it (the scarcest moment first, a fresh row before a vault row, one source a slot) and
cuts the candidate that fits it (the HUD's stars for a chase, a crash and "jump" for a stunt, never a still
opener first); THE VAULT keeps each creator's best GTA 5 uploads of the past year and files rows in the inbox for
the moments no fresh row fits. No skill runs; yt-dlp and run.py are faked."""
import json
import sys
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import board as B          # noqa: E402
from clipfactory import clipformats as CF   # noqa: E402
from clipfactory import config as C         # noqa: E402
from clipfactory import makers as M         # noqa: E402

DAY = date(2026, 10, 31)          # the 6-a-day rule: 07:20 10:50 14:20 17:50 19:35 21:20
LAUNCH = date(2026, 11, 19)       # the 8-a-day rule adds 09:05 and 12:35
NOW = datetime(2026, 10, 29, 23, 0, tzinfo=timezone.utc)


def row(n: int, title: str, channel: str = "", vault: bool = False, **kw) -> dict:
    return dict({"url": f"https://www.youtube.com/watch?v=vid{n:08d}", "status": "new", "kind": "gameplay",
                 "score": 100 - n, "channel": channel or f"ch{n}", "name": f"Creator {n}", "title": title,
                 "duration": 900}, **({"vault": True, "found_by": "vault", "score": 0.01} if vault else {}), **kw)


def cand(n: int, a: float, hud=(), events=(), furniture=(), words: str = "", signals=None) -> dict:
    return {"n": n, "start": a, "end": a + 40, "score": 10 - n, "hud": [{"t": a + 5, "kind": k} for k in hud],
            "events": list(events), "furniture": list(furniture), "hook_draft": words, "opens": "", "closes": "",
            "signals": signals or {}}


class Base(unittest.TestCase):
    def setUp(self):
        self._state = C.STATE
        self.tmp = Path(tempfile.mkdtemp(prefix="clipfactory-formats-"))
        C.STATE = self.tmp / "state"
        raw = json.loads((ROOT / "config" / "accounts.json").read_text())
        raw["drafts_root"] = str(self.tmp / "drafts")
        a = raw["accounts"]["stunts"]
        # the 10-29 vault (each creator's past year, for slow days, fresh rows first), so these tests read the
        # mechanism; the 10-30 settings (all time, any creator, first, until) are tested on their own (AllTime)
        a["vault"] = {"on": True, "per_channel": 15, "max_age_days": 365, "per_moment": 2, "waiting_max": 12}
        a["account_dir"] = str(self.tmp / "Stunts")
        a["skill_dir"] = str(self.tmp / "skill")
        self.raw = raw
        self.reload()
        (self.tmp / "skill" / "work").mkdir(parents=True)
        (self.acct.account_dir / "Publish").mkdir(parents=True)
        self.watchlist([])

    def tearDown(self):
        C.STATE = self._state

    def reload(self):
        p = self.tmp / "accounts.json"
        p.write_text(json.dumps(self.raw))
        self.cfg = C.load(p)
        self.acct = self.cfg.account("stunts")

    def watchlist(self, rows):
        (self.acct.account_dir / "Publish" / "watchlist.json").write_text(json.dumps({"channels": rows}))

    def inbox(self, rows) -> Path:
        p = self.acct.account_dir / "Publish" / "inbox.json"
        p.write_text(json.dumps({"items": {M.video_id(r["url"]): r for r in rows}}))
        return p

    def slots(self, day=DAY):
        return list(B.new_board(self.cfg, self.acct, day)["slots"].values())


class TheBoard(Base):
    def test_a_new_board_writes_each_slots_moment(self):
        got = {s["slot"]: s.get("moment") for s in self.slots()}
        self.assertEqual(got, {"07:20": "stunt", "10:50": "chase", "14:20": "air_stunt", "17:50": "one_in_a_million",
                               "19:35": "fast_drive", "21:20": "drive_time"})
        self.assertTrue(all(s["format"] == "gameplay" for s in self.slots()))   # the lane stays the template
        launch = {s["slot"]: s.get("moment") for s in self.slots(LAUNCH)}
        self.assertEqual((launch["09:05"], launch["12:35"]), ("parkour", "heist"))

    def test_an_account_without_slot_moments_is_as_before(self):
        g = self.cfg.account("gaming")
        self.assertFalse(CF.has_moments(g))
        self.assertTrue(all("moment" not in s for s in B.new_board(self.cfg, g, DAY)["slots"].values()))

    def test_drive_time_is_cut_as_fast_drive_until_its_own_maker(self):
        self.assertEqual(CF.slot_moment({"slot": "21:20", "moment": "drive_time"}), "fast_drive")
        self.assertIsNone(CF.slot_moment({"slot": "07:20"}))           # a board made before the moments: as before
        self.assertEqual(CF.day_moments(self.acct, DAY).count("fast_drive"), 2)

    def test_every_slot_of_every_volume_rule_has_a_moment(self):
        mm = CF.moment_map(self.acct)
        for r in self.acct.raw["volume"]:
            if r.get("until") == r.get("from"):        # 10-30: the board the day shift made before the change
                continue
            self.assertEqual([s for s in r["slots"] if s not in mm], [], r)


class Fit(Base):
    def test_titles_fit_their_moments(self):
        cases = {"30 JETS Vs 1 SUBMARINE CAR in GTA 5": "air_stunt",
                 "Escaping a 5 STAR Police Chase in GTA 5": "chase",
                 "Spectating The Stunt Jump Master Of GTA": "stunt",
                 "Cutting Up In Traffic At 250 MPH | GTA 5": "fast_drive",
                 "GTA 5 Rooftop Parkour Race": "parkour",
                 "The Diamond Casino Heist Getaway": "heist",
                 "1 in a Million GTA 5 Moments": "one_in_a_million"}
        for title, want in cases.items():
            scores = {m: CF.source_fit({"title": title}, m) for m in CF.MOMENTS if m != "drive_time"}
            self.assertEqual(max(scores, key=scores.get), want, (title, scores))
            self.assertGreaterEqual(scores[want], CF.SOURCE_MIN)

    def test_the_roster_says_what_a_creator_is_known_for(self):
        self.watchlist([{"slug": "pilotguy", "moments": ["air_stunt"]},
                        {"slug": "allrounder", "moments": ["chase", "stunt", "fast_drive"]}])
        r = CF.roster_moments(self.acct)
        self.assertEqual(CF.source_fit({"title": "my new video", "channel": "pilotguy"}, "air_stunt", r), 1.5)
        self.assertEqual(CF.source_fit({"title": "my new video", "channel": "other"}, "air_stunt", r), 0.0)
        # known for three things: the name alone fits none of them, the title has to say which
        self.assertLess(CF.source_fit({"title": "30 Police Bikes Vs 3 Bikers", "channel": "allrounder"}, "stunt", r),
                        CF.SOURCE_MIN)
        self.assertGreaterEqual(CF.source_fit({"title": "Escaping the cops", "channel": "allrounder"}, "chase", r),
                                CF.SOURCE_MIN)

    def test_the_hud_and_the_words_pick_the_candidate(self):
        stars = cand(2, 100, hud=("stars", "stars"))
        jump = cand(3, 200, hud=("crash",), words="SEND IT OFF THE RAMP")
        plain = cand(1, 0)
        chase = CF.rank_for_slot(self.acct, "job", [plain, stars, jump], {"slot": "10:50", "moment": "chase"})
        self.assertEqual(chase[0]["n"], 2)
        stunt = CF.rank_for_slot(self.acct, "job", [plain, stars, jump], {"slot": "07:20", "moment": "stunt"})
        self.assertEqual(stunt[0]["n"], 3)
        self.assertEqual([c["n"] for c in CF.rank_for_slot(self.acct, "job", [plain, stars, jump], {"slot": "x"})],
                         [1, 2, 3])                     # no moment: the skill's own order

    def test_with_nothing_fitting_outright_a_clear_lead_still_goes_first(self):
        talk = cand(1, 0, signals={"motion": 0.5})
        moving = cand(2, 300, signals={"motion": 4.0, "flash": 4.0})          # 0.5 ahead for a stunt
        close = cand(3, 600, signals={"motion": 1.0})                          # 0.06 ahead: noise
        got = CF.rank_for_slot(self.acct, "job", [talk, moving, close], {"slot": "07:20", "moment": "stunt"})
        self.assertEqual([c["n"] for c in got], [2, 1, 3])
        got = CF.rank_for_slot(self.acct, "job", [talk, close], {"slot": "07:20", "moment": "stunt"})
        self.assertEqual([c["n"] for c in got], [1, 3])

    def test_frame_one_is_the_action(self):
        still = cand(1, 0, hud=("stars",), events=("still opener 2s",))
        moving = cand(2, 300, hud=("stars",))
        got = CF.rank_for_slot(self.acct, "job", [still, moving], {"slot": "10:50", "moment": "chase"})
        self.assertEqual([c["n"] for c in got], [2, 1])

    def test_the_words_said_in_the_span_count(self):
        d = self.acct.skill_dir / "work" / "job"
        d.mkdir(parents=True)
        (d / "transcript.srt").write_text("1\n00:03:20,000 --> 00:03:24,000\nbro we're flying the jet under the "
                                          "bridge\n\n2\n00:00:05,000 --> 00:00:08,000\nwelcome back everybody\n")
        got = CF.rank_for_slot(self.acct, "job", [cand(1, 0), cand(2, 195)], {"slot": "14:20", "moment": "air_stunt"})
        self.assertEqual(got[0]["n"], 2)


class Deal(Base):
    def plan(self, rows, day=DAY):
        todo = self.slots(day)
        return M.plan_gta(self.acct, todo, rows)

    def test_each_slot_takes_the_source_that_fits_it_one_source_a_slot(self):
        rows = [row(1, "GTA 5 funny moments"), row(2, "Escaping a 5 STAR Police Chase"),
                row(3, "Insane Stunt Jumps in GTA 5"), row(4, "30 JETS Vs 1 SUBMARINE CAR"),
                row(5, "Cutting up in traffic"), row(6, "1 in a million moments"), row(7, "GTA 5 races with the crew")]
        plan, left = self.plan(rows)
        got = {slots[0]["slot"]: item["title"] for item, slots, _t in plan}
        self.assertEqual(left, [])
        self.assertTrue(all(len(slots) == 1 for _i, slots, _t in plan))         # one video, one clip
        self.assertEqual(got["10:50"], "Escaping a 5 STAR Police Chase")
        self.assertEqual(got["07:20"], "Insane Stunt Jumps in GTA 5")
        self.assertEqual(got["14:20"], "30 JETS Vs 1 SUBMARINE CAR")
        self.assertEqual(got["17:50"], "1 in a million moments")
        self.assertIn(got["19:35"], ("Cutting up in traffic", "GTA 5 races with the crew"))
        self.assertTrue(all(t == "gameplay" for _i, _s, t in plan))

    def test_a_fresh_row_that_fits_beats_a_vault_row_and_a_vault_row_that_fits_beats_one_that_does_not(self):
        rows = [row(1, "GTA 5 random stuff"), row(2, "Stunt jumps in GTA 5", vault=True),
                row(3, "Huge police chase", vault=True), row(4, "Police chase all night")]
        plan, _left = self.plan(rows)
        got = {slots[0]["slot"]: item["title"] for item, slots, _t in plan}
        self.assertEqual(got["10:50"], "Police chase all night")              # fresh and fits
        self.assertEqual(got["07:20"], "Stunt jumps in GTA 5")                # no fresh stunt: the vault's
        self.assertEqual(len(plan), 4)                                        # four sources, four slots

    def test_the_scarcest_moment_is_dealt_first(self):
        # the only air source also fits the stunt slot best of all; air goes first, so it keeps it
        rows = [row(1, "Plane stunts and insane jumps"), row(2, "Stunt jumps")]
        plan, _left = self.plan(rows)
        got = {slots[0]["slot"]: item["title"] for item, slots, _t in plan}
        self.assertEqual(got["14:20"], "Plane stunts and insane jumps")
        self.assertEqual(got["07:20"], "Stunt jumps")

    def test_without_moments_the_old_deal_holds(self):
        self.raw["accounts"]["stunts"].pop("slot_moments")
        self.reload()
        rows = [row(1, "a"), row(2, "b")]
        todo = list(B.new_board(self.cfg, self.acct, DAY)["slots"].values())
        plan, _left = M.plan_gta(self.acct, todo, rows)
        self.assertTrue(all("moment" not in s for _i, slots, _t in plan for s in slots))


class TheCut(Base):
    def test_the_slots_moment_picks_the_candidate_run_py_cuts(self):
        job = "factory-clips-20261031-gameplay-x"
        d = self.acct.skill_dir / "work" / job
        d.mkdir(parents=True)
        (d / "moments.json").write_text(json.dumps({"seconds": 1200, "candidates": [
            cand(1, 0, words="WELCOME BACK"), cand(2, 300, hud=("stars", "stars"), words="LOSE THE COPS")]}))
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["10:50"]["status"] = B.MAKING
        slot = B.read(self.acct, DAY)["slots"]["10:50"]
        calls = []

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            calls.append(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        @contextmanager
        def hold(*a, **k):
            yield

        with mock.patch.object(M, "run_logged", fake_run), mock.patch.object(M, "_deliver_slot", lambda *a, **k: []), \
                mock.patch("clipfactory.renderlock.hold", hold):
            M.cut_gta_slots(self.cfg, self.acct, DAY, job, "gameplay", [slot], self.tmp / "log.txt", {})
        self.assertEqual(calls[0][calls[0].index("--pick") + 1], "2")

    def test_the_hook_comes_from_the_video_and_the_kind_goes_into_project_json(self):
        # Andrew 10.30 "Lock it": the 10-30 hooks were lines from the talk ("Oh what the fucking hell?")
        job = "factory-clips-20261031-gameplay-y"
        d = self.acct.skill_dir / "work" / job
        d.mkdir(parents=True)
        (d / "moments.json").write_text(json.dumps({"seconds": 1200, "candidates": [cand(1, 300, hud=("stars",))]}))
        (d / "project.json").write_text(json.dumps({"source_title": "Escaping 5 Stars In A Garbage Truck in GTA 5 (4K)"}))
        with B.edit(self.cfg, self.acct, DAY) as bd:
            bd["slots"]["10:50"]["status"] = B.MAKING
        slot = B.read(self.acct, DAY)["slots"]["10:50"]
        calls = []

        def fake_run(cmd, log, cwd=None, env=None, timeout=0):
            calls.append(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0)

        @contextmanager
        def hold(*a, **k):
            yield

        with mock.patch.object(M, "run_logged", fake_run), mock.patch.object(M, "_deliver_slot", lambda *a, **k: []), \
                mock.patch("clipfactory.renderlock.hold", hold):
            M.cut_gta_slots(self.cfg, self.acct, DAY, job, "gameplay", [slot], self.tmp / "log.txt", {})
        self.assertEqual(calls[0][calls[0].index("--hook") + 1], "ESCAPING 5 STARS IN A GARBAGE TRUCK")
        pj = json.loads((d / "project.json").read_text())
        self.assertEqual((pj["moment"], pj["slot"]), ("chase", "10:50"))


class AllTime(Base):
    """THE VAULT as landed 2026-10-30 (Andrew: "the best things of all time on GTA 5... videos any creator")."""
    def setUp(self):
        super().setUp()
        self.raw["accounts"]["stunts"]["vault"] = {
            "on": True, "all_time": True, "first": True, "until": "2026-11-18", "per_channel": 15, "per_moment": 3,
            "waiting_max": 18, "per_search": 40, "search_min_minutes": 4,
            "searches": {"chase": ["gta 5 police chase"]},
            "skip_titles": r"\b(compilations?|funny moments?|fails?)\b|spider-?man|shin-?chan"}
        self.raw["accounts"]["stunts"]["gta_maker"]["skip_channels"] = ["creator x", "gtaragdolian"]
        self.reload()
        self.watchlist([{"slug": "pilot", "name": "Pilot", "url": "https://www.youtube.com/@pilot", "kind": "gameplay",
                         "platform": "youtube", "phase": "both", "min_minutes": 5, "gta_only": True}])
        old = (NOW - timedelta(days=900)).timestamp()
        self.listing = [{"id": "p1", "title": "GTA 5 Plane Stunts Under Every Bridge", "duration": 900,
                         "view_count": 900_000, "timestamp": old}]
        hit = lambda i, t, ch, v=1000, d=800: {"id": i, "title": t, "duration": d, "view_count": v, "channel": ch}  # noqa: E731
        self.found = [hit("s1", "GTA Wanted Level 100!", "MrBeast Gaming", 111_000_000),
                      hit("s2", "¡NO ELIJAS EL AGUJERO EQUIVOCADO EN GTA V!", "ElTrollino", 90_000_000),
                      hit("s3", "Wild chase involving 3 reported grand theft auto suspects", "ABC7", 40_000_000),
                      hit("s4", "GTA 4 police chase but the cops are FAST", "Someone", 30_000_000),
                      hit("s5", "BEST GTA 5 POLICE CHASE COMPILATION", "Clips Inc", 20_000_000),
                      hit("s6", "GTA 5 Ragdoll Police Chase", "GTARagdolian", 10_000_000),
                      hit("s7", "Escaping Cops in a Garbage Truck in GTA 5", "Mythpat", 5_000_000),
                      hit("s8", "GTA 5 cop chase short", "Shorty", 9_000_000, 50)]

    def build(self):
        return CF.build_vault(self.acct, lister=lambda url, n: self.listing, searcher=lambda q, n: self.found,
                              now=NOW, pause_s=0)

    def test_all_time_and_any_creator(self):
        d = self.build()
        got = {r["id"]: r for r in d["rows"]}
        self.assertIn("p1", got)                                   # 900 days old: all time keeps it
        self.assertEqual(sorted(k for k in got if k.startswith("s")), ["s1", "s7"])   # the rest refused, one reason each
        self.assertIn("chase", got["s1"]["moments"])               # the query's kind, though the title says "wanted"
        self.assertEqual((got["s1"]["channel"], got["s1"]["found_by"]), ("mrbeastgaming", "search: gta 5 police chase"))
        self.assertEqual(d["searches"]["gta 5 police chase"]["kept"], 2)

    def test_the_vault_is_dealt_first_the_most_viewed_first(self):
        rows = [row(1, "Police chase all night"), row(2, "Huge police chase", vault=True, views=5_000_000),
                row(3, "Biggest police chase", vault=True, views=9_000_000)]
        plan, _left = M.plan_gta(self.acct, self.slots(), rows)
        got = {slots[0]["slot"]: item["title"] for item, slots, _t in plan}
        self.assertEqual(got["10:50"], "Biggest police chase")

    def test_the_vault_stops_with_the_gta_6_board(self):
        self.build()
        self.inbox([])
        self.assertTrue(CF.vault_top_up(self.acct, DAY, used=set(), now=NOW))
        self.assertEqual(CF.vault_top_up(self.acct, LAUNCH, used=set(), now=NOW), [])

    def test_first_files_vault_rows_even_when_fresh_rows_fit(self):
        self.build()
        p = self.inbox([row(1, "Police chase all night")])
        CF.vault_top_up(self.acct, DAY, used=set(), now=NOW)
        filed = [v for v in json.loads(p.read_text())["items"].values() if v.get("vault")]
        self.assertTrue(any("chase" in v["moments"] for v in filed))


class Hooks(unittest.TestCase):
    def test_the_hook_says_what_happens(self):
        h = CF.moment_hook
        self.assertEqual(h("30 JETS Vs 1 SUBMARINE CAR in GTA 5", "stunt"), "30 JETS VS 1 SUBMARINE CAR")
        self.assertEqual(h("I Jumped Every Ramp in GTA 5 (IMPOSSIBLE) 🔥", "stunt"), "I JUMPED EVERY RAMP")
        self.assertEqual(h("GTA 5: Can You Escape 5 Stars In A Garbage Truck?", "chase"),
                         "CAN YOU ESCAPE 5 STARS IN A GARBAGE TRUCK?")
        self.assertEqual(h("The Ultimate GTA V Stunt Race!", "stunt"), "THE ULTIMATE GTA 5 STUNT RACE")
        # inside the sentence the game's name is a word of it (10.30 dry run: "CAN YOU BEAT WITH...")
        self.assertEqual(h("Can You Beat GTA 5 With IMPOSSIBLE Ultra Graphics?", "fast_drive"),
                         "CAN YOU BEAT GTA 5 WITH IMPOSSIBLE ULTRA GRAPHICS?")
        self.assertEqual(h("How fast can you RUN across GTA 5? (1000% Run Speed)", "fast_drive"),
                         "HOW FAST CAN YOU RUN ACROSS GTA 5?")
        self.assertEqual(h("Flying Jets Under Every Bridge in Los Santos | GTA 5", "air_stunt"),
                         "FLYING JETS UNDER EVERY BRIDGE IN LOS SANTOS")
        self.assertEqual(h("GTA 5 - Five Star POLICE CHASE with Franklin", "chase"), "FIVE STAR POLICE CHASE WITH FRANKLIN")

    def test_the_kinds_own_line_when_the_title_will_not_do(self):
        h = CF.moment_hook
        self.assertEqual(h("GTA 5 Thug Life #619 (I Tried Robbing Everyone)", "chase"), "CAN THEY LOSE THE COPS?")
        self.assertEqual(h("Oh what the f*ck in GTA 5", "one_in_a_million"), "THIS SHOULD NOT HAVE WORKED")
        self.assertEqual(h("Jumping a Bus Over the Cops in GTA 5 With", "stunt"), "WATCH THE LANDING")
        self.assertEqual(h("", "fast_drive"), "TOP SPEED THROUGH TRAFFIC")
        self.assertEqual(h("Shiitake mushrooms jump", "stunt"), "SHIITAKE MUSHROOMS JUMP")    # not a swear

    def test_english_titles_only(self):
        e = CF.english_title
        self.assertFalse(e("MOTOS com Homem Aranha e Heróis! Saltos na Mega Rampa"))
        self.assertFalse(e("World's Highest 😲 दुनिया का सबसे ऊंचा"))
        self.assertTrue(e("El Burro Heights police chase in GTA 5"))
        self.assertTrue(e("Flying under every bridge in Los Santos 🔥"))


class Vault(Base):
    def setUp(self):
        super().setUp()
        self.watchlist([{"slug": "pilot", "name": "Pilot", "url": "https://www.youtube.com/@pilot", "kind": "gameplay",
                         "platform": "youtube", "phase": "both", "min_minutes": 5, "gta_only": True},
                        {"slug": "creator x", "name": "Creator", "url": "https://www.youtube.com/@t", "kind": "gameplay",
                         "platform": "youtube", "phase": "both"},
                        {"slug": "newsguy", "name": "News", "url": "https://www.youtube.com/@n", "kind": "news",
                         "platform": "youtube", "phase": "both"}])
        ts = lambda d: (NOW - timedelta(days=d)).timestamp()      # noqa: E731
        self.listing = [
            {"id": "a1", "title": "GTA 5 Plane Stunts Under Every Bridge", "duration": 900, "view_count": 900_000, "timestamp": ts(40)},
            {"id": "a2", "title": "GTA 5 Police Chase Gone Wrong", "duration": 700, "view_count": 500_000, "timestamp": ts(100)},
            {"id": "a3", "title": "GTA 6 gameplay first look", "duration": 900, "view_count": 2_000_000, "timestamp": ts(10)},
            {"id": "a4", "title": "GTA 5 leaked map", "duration": 900, "view_count": 800_000, "timestamp": ts(10)},
            {"id": "a5", "title": "GTA 5 short", "duration": 50, "view_count": 700_000, "timestamp": ts(10)},
            {"id": "a6", "title": "GTA 5 old video", "duration": 900, "view_count": 600_000, "timestamp": ts(500)},
            {"id": "a7", "title": "My minecraft world", "duration": 900, "view_count": 999_999, "timestamp": ts(5)},
            {"id": "a8", "title": "GTA 5 Insane Stunt Jumps", "duration": 600, "view_count": 300_000, "timestamp": ts(20)},
        ]

    def build(self):
        asked = []

        def lister(url, n):
            asked.append(url)
            return self.listing
        d = CF.build_vault(self.acct, lister=lister, now=NOW, pause_s=0)
        return d, asked

    def test_build_keeps_a_creators_best_gta_5_uploads_of_the_past_year(self):
        d, asked = self.build()
        self.assertEqual(asked, ["https://www.youtube.com/@pilot"])     # not Creator (skip_channels), not news
        self.assertEqual([r["id"] for r in d["rows"]], ["a1", "a2", "a8"])   # by views; GTA 6, leak, short, old, off topic out
        self.assertIn("air_stunt", d["rows"][0]["moments"])
        self.assertTrue(CF.vault_path(self.acct).exists())

    def test_a_failed_listing_keeps_the_last_build(self):
        self.build()

        def broken(url, n):
            raise RuntimeError("bot check")
        d = CF.build_vault(self.acct, lister=broken, now=NOW, pause_s=0)
        self.assertEqual(len(d["rows"]), 3)
        self.assertIn("error", d["channels"]["pilot"])

    def test_top_up_files_rows_for_the_moments_no_fresh_row_fits(self):
        self.build()
        p = self.inbox([row(1, "Police chase all night"), row(2, "GTA 5 races"), row(3, "Funny moments"),
                        row(4, "More races"), row(5, "Even more"), row(6, "And more")])
        lines = CF.vault_top_up(self.acct, DAY, used=set(), now=NOW)
        items = json.loads(p.read_text())["items"]
        filed = {k: v for k, v in items.items() if v.get("vault")}
        # air and stunt; the chase has one fresh row and gets one vault row more, for a refused pick; two FAST DRIVE
        # slots (19:35, and DRIVE TIME cut as FAST DRIVE) and no fast row in the vault: nothing to file for them
        self.assertEqual(sorted(filed), ["a1", "a2", "a8"])
        self.assertTrue(lines)
        r = filed["a1"]
        self.assertEqual((r["status"], r["found_by"], r["kind"]), ("new", "vault", "gameplay"))
        self.assertNotIn("uploaded", r)                       # a year's best is not held to max_age_days
        self.assertEqual(CF.vault_top_up(self.acct, DAY, used=set(), now=NOW), [])     # nothing twice

    def test_a_source_cut_once_is_never_filed_again(self):
        self.build()
        self.inbox([])
        CF.vault_top_up(self.acct, DAY, used={"https://www.youtube.com/watch?v=a1"}, now=NOW)
        items = json.loads((self.acct.account_dir / "Publish" / "inbox.json").read_text())["items"]
        self.assertNotIn("a1", items)
        self.assertIn("a8", items)

    def test_the_night_reads_vault_rows_like_any_row(self):
        self.build()
        self.inbox([])
        got = M.gta_sources(self.acct, set(), DAY)          # the hook files them first
        self.assertEqual({M.video_id(r["url"]) for r in got}, {"a1", "a2", "a8"})

    def test_off_means_nothing_is_filed(self):
        self.raw["accounts"]["stunts"]["vault"]["on"] = False
        self.reload()
        self.inbox([])
        self.assertEqual(CF.vault_top_up(self.acct, DAY, used=set(), now=NOW), [])
        self.assertEqual(M.gta_sources(self.acct, set(), DAY), [])

    def test_a_moment_two_slots_ask_for_gets_two_rows_and_one_more(self):
        self.listing += [{"id": f"f{i}", "title": f"GTA 5 Cutting Up In Traffic {i}", "duration": 700,
                          "view_count": 100_000 - i, "timestamp": (NOW - timedelta(days=9)).timestamp()} for i in range(5)]
        self.build()
        p = self.inbox([row(1, "Fast traffic run")])        # one fresh row fits FAST DRIVE; two slots want it
        CF.vault_top_up(self.acct, DAY, used=set(), now=NOW)
        filed = [v for v in json.loads(p.read_text())["items"].values() if v.get("vault")]
        fast = [v for v in filed if "fast_drive" in v["moments"]]
        self.assertEqual(len(fast), 2)                      # 2 slots + 1 for a refusal - 1 fresh = 2


class LiveConfig(unittest.TestCase):
    """The stunts block as landed (config/accounts.json)."""
    def test_the_locked_board(self):
        cfg = C.load(ROOT / "config" / "accounts.json")
        a = cfg.account("stunts")
        self.assertEqual(a.clips_per_source, 1)                   # one video, one clip a day
        self.assertEqual(CF.day_moments(a, DAY), ["stunt", "chase", "air_stunt", "one_in_a_million", "fast_drive",
                                                  "fast_drive"])
        self.assertTrue(CF.vault_on(a))
        self.assertTrue(a.raw["bench"]["fill"])
        v = a.raw["vault"]                                        # 10-30: all time, any creator, first, GTA 5 only
        self.assertTrue(v["all_time"] and v["first"] and v["searches"])
        self.assertTrue(CF.vault_live(a, date(2026, 11, 18)) and not CF.vault_live(a, LAUNCH))
        self.assertEqual(a.raw["outlets"]["facebook_reels"]["daily_cap"], 6)
        self.assertIn("gtaragdolian", a.raw["gta_maker"]["skip_channels"])
        self.assertFalse(CF.has_moments(cfg.account("gaming")))


if __name__ == "__main__":
    unittest.main()
