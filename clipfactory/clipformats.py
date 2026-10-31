"""Stunts' formats: each posting slot carries a MOMENT, the kind of clip it posts, and THE VAULT feeds the
moments fresh uploads cannot.

    python3 -m clipfactory.clipformats status stunts [--date D]     the slots' moments and the vault
    python3 -m clipfactory.clipformats deal   stunts [--date D]     how tonight would deal the inbox (dry run)
    python3 -m clipfactory.clipformats rank   stunts <job> --moment stunt   a job's candidates for one moment
    python3 -m clipfactory.clipformats vault build stunts [--listed 150] [--dry-run]
    python3 -m clipfactory.clipformats vault topup stunts [--date D] [--dry-run]

THE BOARD (2026-10-29, Andrew: "Stunts is more like gameplay, right? Like stunts, cars driving fast...
parkour"; "more stunts than anything... the stunt videos, the chases... that is kind of what it's about"; TOP 3
"stupid", FAIL + REPLAY "just like another stunt"). accounts.json "slot_moments" maps a slot to its moment:
07:20 STUNT, 10:50 CHASE, 14:20 AIR STUNT, 17:50 ONE IN A MILLION, 19:35 FAST DRIVE, 21:20 DRIVE TIME (a test),
and from 11-19 09:05 PARKOUR and 12:35 HEIST GETAWAY. board.new_board writes the moment on each new slot.

THE NIGHT. plan_gta deals each slot the source that fits its moment (deal_by_moment: one source per slot with
clips_per_source 1, "one video gives one clip a day"), the scarcest moment first; cut_gta_slots cuts the candidate
that fits it best (rank_for_slot: the HUD the skill reads, the words said in the span, its signals). A slot no
source or candidate fits still gets the best clip there is: six posts a day comes first.

THE VAULT. Each roster creator's best GTA 5 uploads of the past year, by views (Publish/vault.json, built by hand
with `vault build`). Gameplay never goes stale, and the roster posts about five new videos in 36 hours (scout
10-29), short of six slots that each want a different kind of clip. vault_top_up (makers.gta_sources, before
each pass) files vault rows in the inbox for the moments no fresh upload fits, so every guard the inbox has
(cut once, failed intakes, the off-topic switch, skip_channels) holds for them too; a vault row is dealt only
after the fresh rows that fit, and before the fresh rows that do not.
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import config as C

# ------------------------------------------------------------------------------------------------- the moments
# title: words in a source's title (and a vault row's). words: words said in a candidate's span. hud: the HUD
# events the skill reads (moments.json "hud": stars = the wanted level, crash, money = cash in, banner = MISSION
# PASSED / WASTED). signals: the candidate's own 0-5 signals, weighted. cut_as: a moment the gameplay maker cuts as
# another until its own maker exists (DRIVE TIME, 2026-10-29: the podcast-over-a-drive render is its own build).
MOMENTS: Dict[str, dict] = {
    "stunt": {
        "label": "STUNT",
        "title": r"\b(stunts?|stunt jumps?|jumps?|ramps?|mega ?ramps?|flips?|loop|wall ?rides?|trick|airtime|"
                 r"launch(es|ed)?|send(s|ing)? it|skate|bmx)\b",
        "words": r"\b(jump(ed|ing)?|ramp|flip(ped)?|land(ed|ing)?|send it|stunt|trick|airtime|loop|bail)\b",
        "hud": {"crash": 0.8},
        "signals": {"motion": 0.3, "flash": 0.2},
    },
    "chase": {
        "label": "CHASE",
        "title": r"\b(chase[ds]?|chasing|cops?|police|wanted|[3-5] ?stars?|escap(e|es|ed|ing)|getaway|pursuit|"
                 r"manhunt|swat|on the run)\b",
        "words": r"\b(cops?|police|chase|stars|lose (them|him|the cops)|escape|getaway|on us|pull over|swat|run)\b",
        "hud": {"stars": 1.5, "crash": 0.3},
        "signals": {"motion": 0.3, "cuts": 0.2},
    },
    "air_stunt": {
        "label": "AIR STUNT",
        "title": r"\b(planes?|jets?|lazer|hydra|helis?|helicopters?|buzzard|chopper|skydiv\w*|parachut\w*|"
                 r"fly(ing)?|pilot|airport|blimp|oppressor)\b",
        "words": r"\b(planes?|jets?|heli\w*|chopper|fly(ing)?|parachute|skydiv\w*|altitude|bridge|eject|land the)\b",
        "hud": {"crash": 0.3},
        "signals": {"motion": 0.3},
    },
    "one_in_a_million": {
        "label": "ONE IN A MILLION",
        "title": r"\b(one in a million|1 in a million|insane|impossible|luck(y|iest)|unbelievable|craziest|"
                 r"perfect timing|no way|wtf|unreal)\b",
        "words": r"\b(no way|oh my god|omg|what the|how did|did you see|one in a million|clip (it|that)|"
                 r"insane|unbelievable|lucky|let'?s go)\b",
        "hud": {"banner": 0.4, "crash": 0.3},
        "signals": {"energy": 0.6, "verdict": 0.5, "flash": 0.2},
    },
    "fast_drive": {
        "label": "FAST DRIVE",
        "title": r"\b(fast|speed|top speed|mph|km ?h|traffic|cutting up|near miss(es)?|weav\w*|highway|freeway|"
                 r"street rac\w*|drag rac\w*|racing|race|supercars?|hypercars?)\b",
        "words": r"\b(fast|speed|traffic|mph|so close|almost (hit|crashed)|cutting|race|gas|floor it)\b",
        "hud": {"crash": 0.2},
        "signals": {"motion": 0.5, "cuts": 0.2},
    },
    "parkour": {
        "label": "PARKOUR",
        "title": r"\b(parkour|free ?run\w*|rooftops?|roof jumps?|wall ?run\w*|climb\w*|on foot)\b",
        "words": r"\b(parkour|roof|climb|jump(ed)? (off|across)|wall run|ledge)\b",
        "hud": {},
        "signals": {"motion": 0.4},
    },
    "heist": {
        "label": "HEIST GETAWAY",
        "title": r"\b(heists?|robb(ed|ery|ing)|rob|bank|casino|cayo perico|diamond|vault|score|loot|getaway)\b",
        "words": r"\b(heist|rob|bank|money|cash|loot|vault|getaway|the score)\b",
        "hud": {"money": 1.2, "stars": 0.8},
        "signals": {"motion": 0.2},
    },
    "drive_time": {
        "label": "DRIVE TIME",
        "title": r"\b(drive|driving|cruise|cruising)\b",
        "words": r"",
        "hud": {},
        "signals": {},
        "cut_as": "fast_drive",
    },
}
SOURCE_MIN = 1.0       # a source "fits" a moment from this score up
CAND_MIN = 1.0         # a candidate likewise
LEAD = 0.3             # with no candidate at CAND_MIN, the best goes first only this far ahead of the skill's first
STILL_OPENER = -1.5    # frame 1 is the action (the locked look): a still opener counts against the candidate
FURNITURE = -0.5       # an intro or outro span likewise


def has_moments(acct: C.Account) -> bool:
    return bool((acct.raw or {}).get("slot_moments"))


def moment_map(acct: C.Account) -> Dict[str, str]:
    """accounts.json "slot_moments": {"07:20": "stunt", ...}, slots normalized, unknown moments left out."""
    out: Dict[str, str] = {}
    for k, v in ((acct.raw or {}).get("slot_moments") or {}).items():
        try:
            slot = C.normal_slot(str(k))
        except SystemExit:
            continue
        if str(v) in MOMENTS:
            out[slot] = str(v)
    return out


def board_moment(acct: C.Account, slot: str) -> Optional[str]:
    """The moment a NEW board writes on this slot (board.new_board), None for an account without slot_moments."""
    return moment_map(acct).get(slot) if has_moments(acct) else None


def slot_moment(slot: dict) -> Optional[str]:
    """The moment a slot is cut as: its own (the board wrote it), through cut_as. A board made before slot_moments
    has none, and its slots are dealt and cut as before."""
    m = str((slot or {}).get("moment") or "")
    if m not in MOMENTS:
        return None
    return MOMENTS[m].get("cut_as") or m


# ------------------------------------------------------------------------------------------------- the hook
# THE HOOK SAYS WHAT HAPPENS (Andrew 2026-10-30, "Lock it"): the 10-30 board's hooks and titles were lines lifted
# from the talk ("Oh what the fucking hell?", "It's world war iii"), and the same question rode on every post. A
# slot with a moment is cut with a hook written from its VIDEO: the source title with the game, the brackets, the
# episode numbers and the emoji taken out, in capitals for the card (gtacopy makes the title from it, and the
# creator's credit stays). A compilation's title, a swear, or a title too long for the card gives the kind's own
# line. The slot's moment and time go into the job's project.json first, so the kit's question pool for that kind
# asks the comment question (gtacopy.moment_question).
HOOK_LINES = {"stunt": "WATCH THE LANDING", "chase": "CAN THEY LOSE THE COPS?", "air_stunt": "WATCH THIS FLIGHT",
              "one_in_a_million": "THIS SHOULD NOT HAVE WORKED", "fast_drive": "TOP SPEED THROUGH TRAFFIC",
              "parkour": "ROOF TO ROOF", "heist": "THE GETAWAY"}
HOOK_MAX_WORDS = 9
# a hook never stops on one of these (the skill's HOOK gate refuses "a piece of a sentence", and the factory would
# take the next candidate with the same title, and the next): the kind's line instead
HOOK_TAIL_WORDS = {"in", "on", "at", "with", "of", "to", "for", "from", "and", "or", "but", "the", "a", "an", "my",
                   "your", "his", "her", "their", "our", "vs", "into", "onto", "by", "is", "are", "was", "if", "when"}
SWEAR = re.compile(r"\b(f+u+c+k\w*|sh[i1]t\w*|bitch\w*|asshole\w*|dick\w*|piss\w*|bastard\w*|damn\w*|"
                   r"wtf|stfu|crap)\b|\w\*+\w?", re.I)       # the last: a word starred out (f*ck, s**t)
COMPILATION = re.compile(r"\b(compilations?|funny moments?|thug life|fails?\s*(&|and)\s*wins?|wins?\s*(&|and)\s*fails?|"
                         r"best moments?|top\s*\d+|best of|highlights?)\b", re.I)
_HOOK_JUNK = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]|#\w+|\b(4k|8k|60 ?fps|hd|uhd|rtx( on)?|ps5|xbox( series x)?)\b", re.I)
_HOOK_EPISODE = re.compile(r"\b(ep(isode)?\.?\s*\d+|part\s*\d+|#\s*\d+|no\.\s*\d+|day\s*\d+|vol\.?\s*\d+)\b", re.I)
# the game's name comes off only where it labels the title: at the head ("GTA 5: ...") or the tail ("... in GTA 5",
# "... | GTA 5"); inside the sentence it is a word of it ("Can You Beat GTA 5 With...", "Run Across GTA 5?") and stays,
# spelled "GTA 5" (the first cut took it out everywhere: "CAN YOU BEAT WITH...", "HOW FAST CAN YOU RUN ACROSS?")
_GAME_NAME = r"(gta\s*(5|v|five)?(\s*online)?|grand\s+theft\s+auto(\s*(5|v|five))?(\s*online)?)"
_HOOK_GAME_HEAD = re.compile(r"^\s*" + _GAME_NAME + r"\b(?!\s*\d)\s*[:|\-–—]?\s*", re.I)
_HOOK_GAME_TAIL = re.compile(r"\s*([|:\-–—,]\s*|\b(in|on|at|for|from|of)\s+)" + _GAME_NAME + r"\b(?!\s*\d)\s*(?=[?!.…]*\s*$)", re.I)
_HOOK_GAME_MID = re.compile(r"\b(gta\s*(v|five)|grand\s+theft\s+auto\s*(5|v|five))\b", re.I)


def moment_hook(title: str, moment: Optional[str]) -> str:
    """The hook card's words for a slot: what happens, from the source's title (above); the kind's line when the
    title will not do. Capitals, no end punctuation but a question's."""
    fallback = HOOK_LINES.get(str(moment or ""), "WATCH THIS")
    t = str(title or "")
    if not t.strip() or COMPILATION.search(t) or SWEAR.search(t):
        return fallback
    t = re.sub(r"[^\w\s'?!&$%.,:|/…-]", " ", _HOOK_EPISODE.sub(" ", _HOOK_JUNK.sub(" ", t)))   # emoji and symbols
    t = re.sub(r"\s+", " ", t).strip()
    t = _HOOK_GAME_MID.sub("GTA 5", _HOOK_GAME_TAIL.sub("", _HOOK_GAME_HEAD.sub("", t)))
    parts = [x.strip(" -|:,./") for x in re.split(r"\s+[|/\-]\s+|\s*\|\s*|:\s+|!+\s+", t) if x.strip(" -|:,./!")]
    best = next((x for x in parts if len(x.split()) >= 3), parts[0] if parts else "")
    best = re.sub(r"\s+", " ", best).strip(" -|:,./")
    question = bool(re.search(r"\?[?!.…]*$", best))                # "Can You ESCAPE PRISON?!" is a question
    best = best.rstrip("?!.… ")
    words = best.split()
    if len(words) < 3 or len(words) > HOOK_MAX_WORDS or not re.search(r"[A-Za-z]{3}", best) \
            or words[-1].lower() in HOOK_TAIL_WORDS or words[0].lower() in ("and", "or", "but", "so", "because"):
        return fallback
    return best.upper() + ("?" if question else "")


def stamp_job(acct: C.Account, job: str, slot: dict) -> Optional[str]:
    """Before a slot's cut, for an account with moments: the slot's kind and time into the job's project.json (the
    kit's question pool reads them), and the hook for run.py --hook. None for an account without moments, or a job
    with no project.json yet (the skill's own hook stands)."""
    if not has_moments(acct):
        return None
    m = slot_moment(slot)
    p = acct.skill_dir / "work" / job / "project.json"
    try:
        cfg = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    if m:
        cfg["moment"] = m
    cfg["slot"] = str(slot.get("slot") or "")
    tmp = p.with_suffix(".json.stamp-tmp")
    tmp.write_text(json.dumps(cfg, indent=1))
    tmp.replace(p)
    return moment_hook(str(cfg.get("source_title") or ""), m)


def _hits(pattern: str, text: str, cap: int) -> int:
    if not pattern or not text:
        return 0
    return min(cap, len({m.group(0).lower() for m in re.finditer(pattern, text, re.I)}))


def roster_moments(acct: C.Account) -> Dict[str, List[str]]:
    """{channel slug: [moments]}: a watchlist row's "moments" says what the creator is known for."""
    p = acct.account_dir / "Publish" / "watchlist.json"
    try:
        rows = json.loads(p.read_text()).get("channels") or []
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(r.get("slug")): [str(m) for m in r.get("moments") or [] if str(m) in MOMENTS]
            for r in rows if isinstance(r, dict) and r.get("slug")}


def source_fit(item: dict, moment: Optional[str], roster: Optional[Dict[str, List[str]]] = None) -> float:
    """How well an inbox row fits a moment: its title's words (1 each, two at most), the creator's own "moments"
    on the roster (1.5 shared out among them: a creator known for one thing fits it on their name alone, one known
    for three needs the title too), a vault row's own tags (0.5)."""
    if not moment or moment not in MOMENTS:
        return 0.0
    spec = MOMENTS[moment]
    score = float(_hits(spec["title"], str(item.get("title") or ""), 2))
    known = (roster or {}).get(str(item.get("channel") or ""), [])
    if moment in known:
        score += 1.5 / len(known)
    if moment in (item.get("moments") or []):
        score += 0.5
    return score


def candidate_fit(cand: dict, moment: Optional[str], text: str = "") -> float:
    """How well one of a job's candidates (moments.json) fits a moment: the HUD events in its span and its payoff,
    its signals (0-5 each), the words said in it, minus a still opener (frame 1 is the action) or an intro."""
    if not moment or moment not in MOMENTS:
        return 0.0
    spec = MOMENTS[moment]
    score, seen = 0.0, set()
    for e in cand.get("hud") or []:
        w = float(spec["hud"].get(str((e or {}).get("kind")), 0))
        if w:
            score += w if e.get("kind") not in seen else w * 0.25
            seen.add(e.get("kind"))
    pay = (cand.get("payoff") or {}).get("kind")
    if pay and spec["hud"].get(pay):
        score += 0.5
    sig = cand.get("signals") or {}
    for k, w in spec["signals"].items():
        try:
            score += float(w) * min(5.0, float(sig.get(k) or 0)) / 4.0
        except (TypeError, ValueError):
            pass
    said = " ".join(str(cand.get(k) or "") for k in ("hook_draft", "opens", "closes")) + " " + (text or "")
    score += 0.6 * _hits(spec["words"], said, 3)
    if any(str(e).startswith("still opener") for e in cand.get("events") or []):
        score += STILL_OPENER
    if any(f in ("intro", "outro") for f in cand.get("furniture") or []):
        score += FURNITURE
    return round(score, 3)


SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


@functools.lru_cache(maxsize=32)
def _srt(path: str, mtime: float) -> Tuple[Tuple[float, float, str], ...]:
    out, cur, words = [], None, []
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return ()
    for ln in lines + [""]:
        m = SRT_TIME.search(ln)
        if m:
            g = [int(x) for x in m.groups()]
            cur = (g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000)
            words = []
        elif not ln.strip():
            if cur and words:
                out.append((cur[0], cur[1], " ".join(words)))
            cur, words = None, []
        elif cur and not ln.strip().isdigit():
            words.append(ln.strip())
    return tuple(out)


def span_text(job_dir: Path, a: float, b: float) -> str:
    """The words said between a and b (the job's transcript.srt), '' when there is none."""
    p = job_dir / "transcript.srt"
    try:
        cues = _srt(str(p), p.stat().st_mtime)
    except OSError:
        return ""
    return " ".join(t for s, e, t in cues if e > a and s < b)


def rank_for_slot(acct: C.Account, job: str, cands: Optional[List[dict]], slot: dict) -> Optional[List[dict]]:
    """cut_gta_slots' candidates for this slot: the ones that fit its moment first (best fit first), then the rest
    in the skill's own order. Unchanged for a slot with no moment, or a job with no candidates."""
    moment = slot_moment(slot)
    if not moment or not cands:
        return cands
    job_dir = acct.skill_dir / "work" / job
    scored = []
    for k, c in enumerate(cands):
        try:
            text = span_text(job_dir, float(c.get("start") or 0), float(c.get("end") or 0))
        except (TypeError, ValueError):
            text = ""
        scored.append((candidate_fit(c, moment, text), k, c))
    good = sorted((x for x in scored if x[0] >= CAND_MIN), key=lambda x: (-x[0], x[1]))
    rest = [x for x in scored if x[0] < CAND_MIN]
    if not good and rest:
        # nothing fits outright (a stunt video whose talk never says "jump"): the one that fits clearly best still
        # goes first, a clear lead over the skill's own first; a hair's difference is noise, the skill's order stays
        best = max(rest, key=lambda x: (x[0], -x[1]))
        if best[0] - rest[0][0] >= LEAD:
            rest = [best] + [x for x in rest if x is not best]
    return [c for _f, _k, c in good] + [c for _f, _k, c in rest]


def deal_by_moment(acct: C.Account, queue: List[dict], fit: List[dict], st: dict) -> tuple:
    """plan_gta's deal for an account with slot moments: ([(source, [slots])], slots left over). Each slot takes
    the source that fits its moment, the scarcest moment first; a source feeds at most clips_per_source slots
    (1: one video gives one clip a day). Who comes first for a slot: a fresh row that fits, a vault row that fits,
    any fresh row (the skill's order), any vault row. A slot with no moment takes the next source as before."""
    cap = max(1, int(acct.clips_per_source or 1))
    roster = roster_moments(acct)
    order = {id(i): k for k, i in enumerate(fit)}
    # THE VAULT FIRST (Andrew 2026-10-30: "testing it on... the best things of all time on GTA 5"): with vault
    # "first", a vault row that fits comes before a fresh one, the most viewed first
    day0 = date.today()
    for s_ in queue:                            # the board's own posting day, never the wall clock
        try:
            day0 = date.fromisoformat(str(s_.get("publish_at") or "")[:10])
            break
        except ValueError:
            continue
    vfirst = bool(vault_settings(acct).get("first")) and vault_live(acct, day0)

    def pref(i: dict) -> tuple:
        v = bool(i.get("vault"))
        return ((not v), -(i.get("views") or 0)) if vfirst else (v, 0)
    room = {id(i): cap for i in fit}
    takes: Dict[int, List[dict]] = {id(i): [] for i in fit}
    fits: Dict[Tuple[int, str], float] = {}

    def f(i: dict, m: Optional[str]) -> float:
        if not m:
            return 0.0
        k = (id(i), m)
        if k not in fits:
            fits[k] = source_fit(i, m, roster)
        return fits[k]

    def fitting(s: dict) -> int:
        m = slot_moment(s)
        return sum(1 for i in fit if f(i, m) >= SOURCE_MIN) if m else 0

    def take(s: dict, i: dict) -> None:
        room[id(i)] -= 1
        takes[id(i)].append(s)

    # 1. the slots some source fits, the scarcest moment first: each takes the best that fits (fresh, then vault),
    # so a slot nothing fits never takes a source another slot's moment needed
    left = []
    for s in sorted([s for s in queue if fitting(s)], key=lambda s: (fitting(s), s["slot"])):
        m = slot_moment(s)
        ok = [i for i in fit if room[id(i)] > 0 and f(i, m) >= SOURCE_MIN]
        if ok:
            take(s, min(ok, key=lambda i: (pref(i)[0], -f(i, m), pref(i)[1], order[id(i)])))
        else:
            left.append(s)
    # 2. the rest take what is left in the skill's order, a fresh row before a vault row
    rest = []
    for s in sorted(left + [s for s in queue if not fitting(s)], key=lambda s: s["slot"]):
        avail = [i for i in fit if room[id(i)] > 0]
        if avail:
            take(s, min(avail, key=lambda i: (pref(i)[0], pref(i)[1], order[id(i)])))
        else:
            rest.append(s)
    left = rest
    dealt = [(i, sorted(takes[id(i)], key=lambda s: s["slot"])) for i in fit if takes[id(i)]]
    return dealt, sorted(left, key=lambda s: s["slot"])


# ------------------------------------------------------------------------------------------------- THE VAULT
VAULT_DEFAULTS = {
    "on": False,
    "per_channel": 15,      # a creator's best this many (by views)
    "listed": 150,          # newest uploads read per creator (metadata only)
    "max_age_days": 365,    # "the past year"
    "min_views": 0,
    "per_moment": 2,        # rows filed for a moment no fresh upload fits
    "waiting_max": 12,      # vault rows waiting in the inbox at most
    "stale_days": 8,        # status says rebuild after this
    "all_time": False,      # True: no age limit, a video's whole life counts (max_age_days is not read)
    "searches": {},         # {moment: [queries]}: YouTube's most viewed of all time for each, from ANY creator
    "per_search": 40,       # results read per search (metadata only)
    "search_min_minutes": 4,
    "skip_titles": "",      # a title the vault never keeps (compilations of other people's clips, character mods)
    "first": False,         # True: the vault is the main supply, dealt before fresh uploads
    "until": None,          # the last posting day the vault feeds ("2026-11-18": it is all GTA 5)
}
GTA6_ONLY = re.compile(r"\b(gta\s*(6|vi)|grand\s+theft\s+auto\s+(6|vi)|leonida|lucia|jason\s+duval)\b", re.I)
GTA5_TOO = re.compile(r"\b(gta\s*(5|v|five)|grand\s+theft\s+auto\s+(5|v|five)|gta\s*online|gtao|los\s+santos|"
                      r"cayo\s+perico)\b|\bgta\s*\+", re.I)
GTA_ANY = re.compile(r"\b(gta|grand theft|los santos)", re.I)


def vault_settings(acct: C.Account) -> dict:
    out = dict(VAULT_DEFAULTS)
    out.update((acct.raw or {}).get("vault") or {})
    return out


def vault_on(acct: C.Account) -> bool:
    return bool(vault_settings(acct).get("on"))


def vault_path(acct: C.Account) -> Path:
    return acct.account_dir / "Publish" / "vault.json"


def _listing(url: str, n: int) -> List[dict]:
    from . import scout as SC                   # the scout's own metadata-only listing (approximate upload times)
    return SC._yt(url.rstrip("/") + "/videos", n, timeout=180)


VIEWS_SP = "CAM%253D"                           # YouTube's search sorted by view count, all time


def _search(query: str, n: int) -> List[dict]:
    from . import scout as SC
    import urllib.parse
    return SC._yt(f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}&sp={VIEWS_SP}", n,
                  timeout=180)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


# AN ENGLISH TITLE (2026-10-30): the all-time searches brought Spanish, Portuguese, Hindi and Italian channels to the
# top ("Saltos na Mega Rampa com SUPER MOTOS", a Devanagari title, "GTA 5 ITA"): their talk would caption as
# nonsense on an English account. A letter outside ASCII (an accent, another script; emoji are not letters) or one
# of these words keeps a video out of the vault.
NOT_ENGLISH = re.compile(r"\b(ita|eng sub|hindi|bhai|sobrevive|rampa|carros|motos|homem|aranha|con|com|um|uma|una|"
                         r"para|pra|policia|persecucion|fuga|ladron|mejor|nuevo|nova|novo|jogo|juego)\b", re.I)


SPANISH_STOP = re.compile(r"\b(el|la|los|las|en|de|del|por|que|y|es|su|al)\b", re.I)


def english_title(title: str) -> bool:
    import unicodedata
    t = str(title or "")
    if any(ord(ch) > 127 and unicodedata.category(ch).startswith("L") for ch in t) or re.search("[¡¿]", t):
        return False
    if NOT_ENGLISH.search(t):
        return False
    # two different Spanish small words ("el", "en", "de"...): "Los Santos" or "El Burro Heights" alone stay English
    return len({w.lower() for w in SPANISH_STOP.findall(re.sub(r"\b(los santos|el burro|la mesa|del perro|"
                                                                  r"la puerta|el rancho|vespucci)\b", " ", t, flags=re.I))}) < 2


# A SEARCH ROW NAMES THE GAME (2026-10-30): "grand theft auto suspects" was a TV station's real police chase, and the
# older games share the name: a search row needs GTA 5 / GTA V / GTA Online / plain "GTA" / Los Santos in its title,
# and none of the other games unless GTA 5 is named too
GTA_GAME = re.compile(r"\bgta\b|\bgta\s*(5|v|online)\b|grand theft auto\s*(5|v|five|online)\b|los santos", re.I)
OTHER_GTA = re.compile(r"\b(gta\s*(4|iv|iii|3|2|sa|vc)|san andreas|liberty city|chinatown wars|the city)\b", re.I)


def vault_live(acct: C.Account, day: date) -> bool:
    """The vault feeds this posting day: on, and not past its "until" (2026-11-18: the vault is GTA 5)."""
    st = vault_settings(acct)
    if not st.get("on"):
        return False
    until = str(st.get("until") or "")
    return not until or day.isoformat() <= until


def vault_row(e: dict, ch: dict, now: datetime) -> Optional[dict]:
    """One upload as a vault row, None when it is not a GTA 5 gameplay video worth keeping."""
    title = str(e.get("title") or "")
    vid = e.get("id")
    dur = e.get("duration") or 0
    if not vid or (ch.get("gta_only", True) and not GTA_ANY.search(title)):
        return None
    if GTA6_ONLY.search(title) and not GTA5_TOO.search(title):        # no GTA 6 gameplay exists before 11-19
        return None
    if re.search(r"\b(leak|leaks|leaked|leaker|leaking)\b|\bcyber ?leek", title, re.I):
        return None
    if dur < 60 * float(ch.get("min_minutes") or 5) or "/shorts/" in str(e.get("url") or ""):
        return None
    ts = e.get("timestamp") or e.get("release_timestamp")
    up = datetime.fromtimestamp(ts, timezone.utc) if ts else None
    return {"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": title,
            "channel": ch.get("slug"), "name": ch.get("name"), "duration": dur, "views": e.get("view_count") or 0,
            "uploaded": up.strftime("%Y-%m-%dT%H:%M:%SZ") if up else None,
            "moments": [m for m in MOMENTS if MOMENTS[m].get("cut_as") is None
                        and source_fit({"title": title}, m) >= SOURCE_MIN]}


def build_vault(acct: C.Account, listed: Optional[int] = None, lister: Callable[[str, int], List[dict]] = None,
                now: Optional[datetime] = None, write: bool = True, pause_s: float = 2.0,
                searcher: Callable[[str, int], List[dict]] = None) -> dict:
    """Read each roster creator's newest uploads (metadata only) and keep their best GTA 5 videos of the past year,
    by views (all_time: of their whole listing); then, with "searches", YouTube's most viewed of all time for each
    moment's queries, from ANY creator (Andrew 2026-10-30: "the best things of all time on GTA 5... videos any
    creator"). A creator whose listing fails keeps its rows from the last build; a search that fails, its rows."""
    st = vault_settings(acct)
    now = now or datetime.now(timezone.utc)
    lister = lister or _listing
    searcher = searcher or _search
    skip_re = re.compile(str(st.get("skip_titles")), re.I) if st.get("skip_titles") else None
    n = int(listed or st["listed"])
    try:
        rows = json.loads((acct.account_dir / "Publish" / "watchlist.json").read_text()).get("channels") or []
    except (OSError, ValueError, AttributeError):
        rows = []
    from . import makers as M
    gst = M.gta_settings(acct)
    playing, skip = M.playing_kinds(gst), set(gst.get("skip_channels") or [])
    try:
        old = json.loads(vault_path(acct).read_text())
    except (OSError, ValueError):
        old = {}
    out, chans = [], {}
    oldest = (datetime(1970, 1, 1, tzinfo=timezone.utc) if st.get("all_time")
              else now - timedelta(days=float(st["max_age_days"])))
    for ch in rows:
        if not isinstance(ch, dict) or ch.get("platform", "youtube") != "youtube" or ch.get("phase") == "off":
            continue
        if ch.get("kind") not in playing or ch.get("slug") in skip:
            continue
        try:
            got = lister(str(ch["url"]), n)
        except Exception as e:                  # one creator's failure never empties the vault
            got, chans[ch["slug"]] = None, {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        if got is None or (not got and ch["slug"] in (old.get("channels") or {})):
            kept = [r for r in old.get("rows") or [] if r.get("channel") == ch["slug"]]
            out += kept
            chans.setdefault(ch["slug"], {})["kept_from_last_build"] = len(kept)
            continue
        keep = []
        for e in got:
            r = vault_row(e, ch, now)
            if not r or (r["uploaded"] and r["uploaded"] < oldest.strftime("%Y-%m-%dT%H:%M:%SZ")):
                continue
            if (r["views"] or 0) < float(st["min_views"] or 0):
                continue
            if (skip_re and skip_re.search(r["title"])) or not english_title(r["title"]):
                continue
            keep.append(r)
        keep.sort(key=lambda r: -(r["views"] or 0))
        keep = keep[: int(st["per_channel"])]
        out += keep
        chans[ch["slug"]] = {"listed": len(got), "kept": len(keep)}
        if pause_s:
            time.sleep(pause_s)
    seen = {r["id"] for r in out}
    searched: Dict[str, dict] = {}
    for m, queries in (st.get("searches") or {}).items():
        if m not in MOMENTS:
            continue
        for q in queries or []:
            try:
                got = searcher(str(q), int(st["per_search"]))
            except Exception as e:              # one search's failure keeps what it found last time
                kept = [r for r in old.get("rows") or [] if r.get("found_by") == f"search: {q}" and r["id"] not in seen]
                out += kept
                seen |= {r["id"] for r in kept}
                searched[str(q)] = {"error": f"{type(e).__name__}: {str(e)[:120]}", "kept_from_last_build": len(kept)}
                continue
            n_keep = 0
            for e in got:
                name = str(e.get("channel") or e.get("uploader") or "")
                ch = {"slug": _slug(name), "name": name, "gta_only": True,
                      "min_minutes": float(st.get("search_min_minutes") or 4)}
                if not ch["slug"] or ch["slug"] in skip:
                    continue
                r = vault_row(e, ch, now)
                if not r or r["id"] in seen or (skip_re and skip_re.search(r["title"])) or not english_title(r["title"]):
                    continue
                if not GTA_GAME.search(r["title"]) or (OTHER_GTA.search(r["title"]) and not GTA5_TOO.search(r["title"])):
                    continue
                if (r["views"] or 0) < float(st["min_views"] or 0):
                    continue
                if m not in r["moments"]:
                    r["moments"].append(m)
                r["found_by"] = f"search: {q}"
                out.append(r)
                seen.add(r["id"])
                n_keep += 1
            searched[str(q)] = {"moment": m, "listed": len(got), "kept": n_keep}
            if pause_s:
                time.sleep(pause_s)
    data = {"account": acct.slug, "built": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": out, "channels": chans,
            "searches": searched,
            "_how": "THE VAULT (clipformats.py): each roster creator's best GTA 5 uploads of the past year, by "
                    "views. The night files rows from it into inbox.json for the moments no fresh upload fits "
                    "(vault_top_up); a row cut once never comes back (its inbox row is 'cut'). Rebuild weekly: "
                    "python3 -m clipfactory.clipformats vault build <account>."}
    if write:
        p = vault_path(acct)
        tmp = p.with_suffix(".json.vault-tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(p)
    return data


def _inbox(acct: C.Account) -> Tuple[Path, dict]:
    p = acct.account_dir / "Publish" / "inbox.json"
    try:
        return p, json.loads(p.read_text())
    except (OSError, ValueError):
        return p, {"items": {}}


def day_moments(acct: C.Account, day: date) -> List[str]:
    """The moments the day's slots carry (the volume rule's slots, through slot_moments and cut_as)."""
    mm = moment_map(acct)
    out = []
    for s in acct.slots_for(day):
        m = mm.get(s)
        if m:
            out.append(MOMENTS[m].get("cut_as") or m)
    return out


def vault_top_up(acct: C.Account, day: date, used: Optional[set] = None, now: Optional[datetime] = None,
                 dry_run: bool = False) -> List[str]:
    """File vault rows in the inbox for the day's moments no waiting fresh row fits (per_moment each), and enough
    for every slot to have a source, up to waiting_max vault rows waiting. Never a source already cut (used, or any
    row the inbox already holds), never a creator taken off (skip_channels)."""
    st = vault_settings(acct)
    if not vault_live(acct, day):
        return []
    try:
        vault = json.loads(vault_path(acct).read_text())
    except (OSError, ValueError):
        return [f"{acct.name}: no vault yet ({vault_path(acct)}); python3 -m clipfactory.clipformats vault build "
                f"{acct.slug}"]
    from . import makers as M
    gst = M.gta_settings(acct)
    used = set(used or ())
    p, data = _inbox(acct)
    items = data.get("items")
    if not isinstance(items, dict):
        items = {M.video_id(i.get("url")) or str(k): i for k, i in enumerate(items or []) if isinstance(i, dict)}
        data["items"] = items
    rows = [i for i in items.values() if isinstance(i, dict)]
    known = {i.get("url") for i in rows} | {M.video_id(i.get("url")) for i in rows}
    waiting = [i for i in rows if i.get("status") == "new" and i.get("url") not in used
               and not M.skipped_title(i, gst) and str(i.get("channel") or "") not in (gst.get("skip_channels") or [])]
    fresh = [i for i in waiting if not i.get("vault")]
    queued = [i for i in waiting if i.get("vault")]
    roster = roster_moments(acct)
    need = day_moments(acct, day)
    slots = len(acct.slots_for(day))
    pool = [r for r in vault.get("rows") or [] if r.get("url") not in used and r.get("url") not in known
            and r.get("id") not in known and r.get("id") not in used and not M.skipped_title(r, gst)
            and str(r.get("channel") or "") not in (gst.get("skip_channels") or [])]
    pool.sort(key=lambda r: -(r.get("views") or 0))
    room = max(0, int(st["waiting_max"]) - len(queued))
    add: List[dict] = []
    first = bool(st.get("first"))         # the vault is the main supply: fresh rows do not count against it
    for m, n in ((m, need.count(m)) for m in dict.fromkeys(need)):     # each moment once, in the day's order
        have = sum(1 for i in (queued if first else fresh + queued) if source_fit(i, m, roster) >= SOURCE_MIN)
        want = min(n + 1, int(st["per_moment"]) * n) - have        # its slots, and one more for a refused pick
        for r in [r for r in pool if r not in add and source_fit(r, m, roster) >= SOURCE_MIN][: max(0, want)]:
            if len(add) < room:
                add.append(r)
    short = slots - (0 if first else len(fresh)) - len(queued) - len(add)
    for r in pool:
        if short <= 0 or len(add) >= room:
            break
        if r not in add:
            add.append(r)
            short -= 1
    now = now or datetime.now(timezone.utc)
    lines = []
    for r in add:
        key = r.get("id") or M.video_id(r.get("url"))
        items[key] = {"channel": r.get("channel"), "name": r.get("name"), "platform": "youtube",
                      "kind": M.found_kind(acct),       # the account's first playing kind ("gameplay")
                      "title": r.get("title"), "url": r.get("url"), "duration": r.get("duration"),
                      "views": r.get("views"), "was_live": False,
                      # no "uploaded": the night's max_age_days is for fresh uploads, and a vault row is a year's best
                      "vault_uploaded": r.get("uploaded"), "seen": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "status": "new", "score": 0.01, "base_score": 0.01, "found_by": "vault", "vault": True,
                      "moments": r.get("moments") or []}
        lines.append(f"  vault -> inbox: {r.get('name')}: {r.get('title')} ({', '.join(r.get('moments') or []) or 'any'})")
    if add and not dry_run:
        tmp = p.with_suffix(".json.vault-tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        tmp.replace(p)
    return lines


# ------------------------------------------------------------------------------------------------- the commands
def _status(cfg: C.Config, acct: C.Account, day: date) -> List[str]:
    out = [f"{acct.name}, posting day {day}:"]
    mm = moment_map(acct)
    for s in acct.slots_for(day):
        m = mm.get(s)
        out.append(f"  {s}  {MOMENTS[m]['label'] if m else '(no moment)'}"
                   + (f"  (cut as {MOMENTS[MOMENTS[m]['cut_as']]['label']} until its own maker)" if m and
                      MOMENTS[m].get('cut_as') else ""))
    st = vault_settings(acct)
    try:
        v = json.loads(vault_path(acct).read_text())
        built = datetime.strptime(v["built"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - built).days
        by = {}
        for r in v.get("rows") or []:
            for m in r.get("moments") or ["(any)"]:
                by[m] = by.get(m, 0) + 1
        out.append(f"  vault: {'on' if st['on'] else 'OFF'}, {len(v.get('rows') or [])} rows from "
                   f"{len(v.get('channels') or {})} creators, built {v['built']}"
                   + (f"  REBUILD: {age} days old" if age > int(st["stale_days"]) else ""))
        out.append("  vault by moment: " + ", ".join(f"{k} {n}" for k, n in sorted(by.items())))
    except (OSError, ValueError, KeyError):
        out.append(f"  vault: {'on' if st['on'] else 'OFF'}, not built yet")
    return out


def _deal(cfg: C.Config, acct: C.Account, day: date) -> List[str]:
    from . import makers as M
    from . import board as B
    bd = B.read(acct, day) or B.new_board(cfg, acct, day)
    todo = [s for s in bd["slots"].values() if s.get("status") in (B.OPEN, B.FAILED) and not s.get("spare")]
    sources = M.gta_sources(acct, M.used_sources(acct), day)
    plan, left = M.plan_gta(acct, todo, sources)
    roster = roster_moments(acct)
    out = [f"{acct.name} {day}: {len(todo)} open slot(s), {len(sources)} waiting source(s)"]
    for item, slots, tpl in plan:
        for s in slots:
            m = slot_moment(s)
            out.append(f"  {s['slot']}  {(MOMENTS[m]['label'] if m else '-'):<16} <- "
                       f"{'[vault] ' if item.get('vault') else ''}{item.get('name') or item.get('channel')}: "
                       f"{str(item.get('title'))[:70]}  (fit {source_fit(item, m, roster):.1f})")
    out += [f"  {s['slot']}  no source" for s in left]
    return out


def _rank(acct: C.Account, job: str, moment: str) -> List[str]:
    from . import makers as M
    cands, skipped = M.gta_candidates(acct, job)
    if cands is None:
        return [f"{job}: no moments.json"]
    ranked = rank_for_slot(acct, job, cands, {"slot": "00:00", "moment": moment})
    jd = acct.skill_dir / "work" / job
    out = [f"{job}, for {MOMENTS[moment]['label']}:"]
    for c in ranked:
        fit = candidate_fit(c, MOMENTS[moment].get("cut_as") or moment,
                            span_text(jd, float(c.get("start") or 0), float(c.get("end") or 0)))
        out.append(f"  #{c['n']:<3} fit {fit:5.2f}  {float(c.get('start') or 0):7.1f}s  "
                   f"{', '.join(e.get('label', '') for e in c.get('hud') or []) or '-':<24} {c.get('hook_draft', '')[:50]}")
    return out + [f"  ({x})" for x in skipped]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="clipformats", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "deal"):
        s = sub.add_parser(name)
        s.add_argument("account")
        s.add_argument("--date")
    r = sub.add_parser("rank")
    r.add_argument("account")
    r.add_argument("job")
    r.add_argument("--moment", required=True, choices=sorted(MOMENTS))
    v = sub.add_parser("vault")
    v.add_argument("what", choices=("build", "topup", "status"))
    v.add_argument("account")
    v.add_argument("--date")
    v.add_argument("--listed", type=int)
    v.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cfg = C.load()
    acct = cfg.account(a.account)
    day = C.parse_day(getattr(a, "date", None), cfg)
    if a.cmd == "status":
        print("\n".join(_status(cfg, acct, day)))
    elif a.cmd == "deal":
        print("\n".join(_deal(cfg, acct, day)))
    elif a.cmd == "rank":
        print("\n".join(_rank(acct, a.job, a.moment)))
    elif a.what == "build":
        d = build_vault(acct, listed=a.listed, write=not a.dry_run)
        print(f"{acct.name}: vault {len(d['rows'])} rows from {len(d['channels'])} creators"
              + (" (dry run: not written)" if a.dry_run else f" -> {vault_path(acct)}"))
        for slug, c in sorted(d["channels"].items()):
            print(f"  {slug}: {c}")
    elif a.what == "topup":
        from . import makers as M
        lines = vault_top_up(acct, day, M.used_sources(acct), dry_run=a.dry_run)
        print("\n".join(lines) or f"{acct.name}: nothing to file (fresh rows fit the day, or the vault is off)")
    else:
        print("\n".join(_status(cfg, acct, day)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
