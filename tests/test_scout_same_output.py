"""The scout's GTA and Politics rules decide by the account's maker since 2026-10-19 (the two new GTA accounts), not by
the slug "gaming". This pins the output for Gaming and Politics: one fixed set of finds, ranked and
written as a board, must come out byte for byte as it did at 74e2095, before the change (the golden file was
written by that code: `python3 tests/test_scout_same_output.py --write`). The rules themselves are frozen too
(tests/fixtures/scout_same_rules.json, a copy of both blocks without their dated `phases`), so a later edit of
config/scout.json does not move this test. Offline: nothing is fetched."""
from __future__ import annotations

import os as _os
_os.environ["CLIP_FACTORY_OFFLINE"] = "1"   # no test may ever reach a real account (clipfactory/net.py)
import tempfile as _tempfile
_os.environ["CLIP_FACTORY_STATE"] = _tempfile.mkdtemp(prefix="clipfactory-scout-same-state-")

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clipfactory import config as C     # noqa: E402
from clipfactory import scout as SC     # noqa: E402

FIX = ROOT / "tests" / "fixtures"
RULES = FIX / "scout_same_rules.json"
GOLDEN = FIX / "scout_same_output.json"
NOW = datetime(2031, 3, 4, 18, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat(timespec="seconds")
ROSTER = [  # the GTA roster the fixture's watchlist holds (prelaunch_until far off: the phase never moves)
    {"slug": "kaicenat", "name": "Kai Cenat", "url": "https://www.youtube.com/@KaiCenat", "platform": "youtube",
     "kind": "streamer", "weight": 1.2, "phase": "both", "min_minutes": 5},
    {"slug": "speirs", "name": "Speirs", "url": "https://www.youtube.com/@Speirs", "platform": "youtube",
     "kind": "physics", "weight": 1.0, "phase": "both", "min_minutes": 5},
    {"slug": "tgg", "name": "TGG", "url": "https://www.youtube.com/@TGG_", "platform": "youtube",
     "kind": "breakdown", "weight": 1.0, "phase": "both", "min_minutes": 5},
]


def vid(i, title, channel, vph=None, age=2.0, views=None, duration=600, given=None, slug=None, **kw) -> dict:
    f = {"id": i, "kind": "video", "url": f"https://www.youtube.com/watch?v={i}", "title": title, "channel": channel,
         "channel_slug": slug, "source": channel, "given_rights": given, "weight": 1.0,
         "published": "2031-03-04T16:00:00+00:00", "age_h": age, "views": views, "vph": vph, "duration": duration,
         "was_live": False, "short": bool(duration and duration <= 70)}
    f.update(kw)
    return f


def art(i, title, channel, age=3.0, given=None) -> dict:
    return {"id": i, "kind": "article", "url": f"https://example.com/{i}", "title": title, "channel": channel,
            "source": channel, "given_rights": given or "network", "weight": 1.0,
            "published": "2031-03-04T15:00:00+00:00", "age_h": age}


def gta_raw() -> list:
    return [
        vid("kai00000001", "Kai Cenat Reacts To GTA 6 Trailer 3!", "Kai Cenat", vph=40000, age=3, views=120000,
            duration=900),
        vid("leak0000001", "GTA 6 Price LEAKED by an insider", "Leak Hub", vph=9000, age=5, views=45000),
        vid("speirs00001", "GTA 5 Online insane police chase", "Speirs", vph=2500, age=6, views=15000),
        vid("moist000001", "I hated the GPS in GTA 6", "penguinz0", vph=60000, age=2, views=120000),
        vid("tgg00000001", "GTA 6 map size compared to every GTA", "TGG", vph=3000, age=10, views=30000,
            duration=1500),
        art("adex0000001", "Rockstar confirms GTA 6 release date again", "Dexerto GTA", age=4),
        vid("fort0000001", "Fortnite update live reaction", "Some Streamer", vph=80000, age=1, views=80000),
        vid("xqc00000001", "xQc reacts to GTA 6 gameplay", "ImStillxQc", vph=12000, age=8, views=96000,
            given="roster", slug="imstillxqc", roster_kind="reaction", min_minutes=3, person="xQc"),
        vid("short000001", "GTA 6 in 30 seconds #shorts #gta6 #gaming", "Clip Farm", vph=5000, age=4, views=20000,
            duration=45),
        vid("comp0000001", "GTA 6 trailer 3 reaction compilation", "Reacts Daily", vph=7000, age=6, views=42000),
        vid("old00000001", "GTA 6 trailer 3 breakdown", "Old Channel", vph=900, age=50, views=45000),
        vid("rock0000001", "Grand Theft Auto VI Trailer 3", "Rockstar Games", vph=300000, age=1, views=300000,
            duration=150),
    ]


def politics_raw() -> list:
    return [
        vid("warren00001", "Warren yells at bank CEO who will not answer", "C-SPAN", vph=20000, age=2, views=40000),
        vid("kennedy0001", 'Sen. Kennedy: "You should reconsider your whole career" to nominee', "Senator John Kennedy",
            vph=8000, age=4, views=32000),
        vid("paris000001", "Trump clashes with reporters in Paris", "CBS News", vph=15000, age=3, views=45000),
        vid("sched000001", "BREAKING NEWS LIVE: committee schedule", "Some Channel", vph=500, age=1, views=500),
        vid("lens0000001", "Senate hearing erupts", "Washington Lens", vph=30000, age=2, views=60000),
        art("ahouse00001", "House passes the spending bill after a heated night", "Politico", age=5),
        vid("game0000001", "Spartans win the game", "ESPN", vph=90000, age=2, views=180000),
        vid("hegs0000001", "Hegseth grilled over boat strikes", "NBC News", vph=150000, age=3, views=450000),
        vid("hegs0000002", "Hegseth grilled by senators over the boat strikes", "Fox News", vph=40000, age=3,
            views=120000),
        vid("recap000001", "Hegseth hearing recap: what it means", "CNN", vph=20000, age=4, views=80000),
    ]


def accounts(tmp: Path):
    gdir = tmp / "gta"
    (gdir / "Publish").mkdir(parents=True, exist_ok=True)
    (gdir / "Publish" / "watchlist.json").write_text(json.dumps({"prelaunch_until": "2099-01-01",
                                                                "channels": ROSTER}))
    common = dict(delivered=[], slots=["07:00"], daily_target=1, clips_per_source=3, platforms=[], outlets={})
    gta = C.Account(slug="gaming", name="Gaming", handle="@gaming", maker="gta", skill_dir=tmp,
                    account_dir=gdir, min_seconds=30, max_seconds=60, formats=["underlay"], **common)
    led = C.Account(slug="politics", name="Politics", handle="", maker="politics", skill_dir=tmp,
                    account_dir=tmp / "politics", min_seconds=65, max_seconds=90, formats=["room"], **common)
    cfg = C.Config(timezone="America/Denver", drafts_root=tmp / "drafts", review_port=0, approvers=["Andrew"],
                   accounts={"gaming": gta, "politics": led}, path=tmp / "accounts.json")
    return cfg, gta, led


def build() -> dict:
    """What the scout makes of the fixture's finds for each account: the ranked finds, the feed pool, the board."""
    tmp = Path(tempfile.mkdtemp(prefix="clipfactory-scout-same-"))
    old = SC.SCOUT_CONFIG
    SC.SCOUT_CONFIG = RULES
    try:
        cfg, gta, led = accounts(tmp)
        out = {}
        for acct, raw in ((gta, gta_raw()), (led, politics_raw())):
            scfg = SC.load(acct.slug, today="2031-03-04")
            finds, stories, wave = SC.rank(raw, scfg, acct, float(scfg.get("hours", 36)), {}, {}, {}, STAMP, now=NOW)
            board = {"account": acct.slug, "name": acct.name, "date": "2031-03-04", "updated": STAMP,
                     "hours": float(scfg.get("hours", 36)), "sources": 20, "failed": [], "fallback": [],
                     "seconds": 1.0, "wave": wave, "finds": {f["id"]: f for f in finds}}
            md = SC.write_markdown(cfg, acct, board).read_text()
            out[acct.slug] = {"finds": json.loads(json.dumps(board["finds"], sort_keys=True, default=str)),
                              "stories": len(stories), "pool": [f["id"] for f in SC.feed_pool(finds, scfg)],
                              "md": md}
        return out
    finally:
        SC.SCOUT_CONFIG = old


class SameOutput(unittest.TestCase):
    def test_gaming_and_politics_rank_and_write_as_before(self):
        want = json.loads(GOLDEN.read_text())
        got = build()
        for slug in ("gaming", "politics"):
            self.assertEqual(got[slug]["md"], want[slug]["md"], slug)          # the board's words, byte for byte
            self.assertEqual(got[slug]["finds"], want[slug]["finds"], slug)    # every find: lane, breaking, score...
            self.assertEqual((got[slug]["stories"], got[slug]["pool"]), (want[slug]["stories"], want[slug]["pool"]))

    def test_the_fixture_reaches_both_rules(self):
        want = json.loads(GOLDEN.read_text())
        g, led = want["gaming"]["finds"], want["politics"]["finds"]
        self.assertEqual({f["lane"] for f in g.values() if f["kind"] == "video"}, {"roster", "creator"})
        self.assertIn("fort0000001", {f["id"] for f in gta_raw()})
        self.assertNotIn("fort0000001", g)                     # off topic: GTA keeps the on-topic only
        self.assertTrue(any(f["breaking"] for f in g.values()))
        self.assertTrue({"cut", "find original"} <= {f["lane"] for f in led.values()})
        self.assertTrue(any(f["breaking"] for f in led.values()))
        self.assertIn("## Hottest", want["gaming"]["md"])
        self.assertIn("**find original**", want["politics"]["md"])


def write_golden() -> None:
    data = json.loads((ROOT / "config" / "scout.json").read_text())
    rules = {}
    for slug in ("gaming", "politics"):
        b = dict(data[slug])
        b.pop("phases", None)                  # dated settings would move the rules by the calendar
        rules[slug] = b
    FIX.mkdir(parents=True, exist_ok=True)
    RULES.write_text(json.dumps(rules, indent=1, ensure_ascii=False) + "\n")
    GOLDEN.write_text(json.dumps(build(), indent=1, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--write"]:
        write_golden()
        print(f"wrote {RULES} and {GOLDEN}")
    else:
        unittest.main()
