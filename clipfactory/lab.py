"""Lab (2026-10-20, Andrew: "Build the secrets and what-happens-if tests channel. I have a console and can
record."): one question per 30 to 60 second clip, answered on Andrew's OWN console recording, the account's own voice
explaining. The maker "lab" (makers.make dispatches here) and everything around it:

    the bank       Publish/tests.json in the account folder: one row per question (todo -> recorded -> verified ->
                   scripted -> rendered -> placed -> posted; held, relook, redo and dropped on the side). Written only
                   here (bank_edit: an fcntl lock beside it, a temp file, os.replace), by the Lab tab and factory.py
                   lab; never by hand, never by the Claude task
    capture        OBS over obs-websocket (obs.py): Record, Mark, Stop on the Lab tab; the take is renamed into the
                   capture root as <date>/T001.mp4 (take 2: T001-2.mp4) with its sidecar T001.json. The fallback is
                   the console's own recording copied into <capture root>/inbox (inbox_scan)
    the truth      an outcome (As expected / Different / Redo) is set ONLY by a browser request from the review page
                   (review._from_page), by a name on approvers, on a take with at least one mark, against the seen key
                   of what the page showed. No command and no bot can set one. The bank's expected answer is a
                   hypothesis shown to him, never a claim on its own
    evidence       frames() cuts the frames around each mark (factory.py lab frames); the Claude task reads them and
                   writes T001.script.json; accept_script() checks it against the take, the marks, the outcome and
                   the variety rules (factory.py lab script), and only then is it scripted
    the maker      make(): for each open slot a scripted test that fits the day (post_games, variety), rendered by
                   the skill's TEST lane (scripts/labreel.py build) in the render queue, then factory.py place as an
                   unsigned draft. Lab clips never sign themselves (auto_sign off): Andrew approves each one

Everything is per account (an account whose maker is "lab"), read from its "lab" block in config/accounts.json.
"""
from __future__ import annotations

import contextlib
import copy
import difflib
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from . import board as B
from . import config as C

MAKER = "lab"
STATUSES = ("todo", "held", "recorded", "verified", "relook", "redo", "scripted", "rendered", "placed", "posted",
            "dropped")
RECORDABLE = ("todo", "redo", "recorded", "relook")      # a new take; never once scripted, rendered, placed, posted
MARKABLE = ("recorded", "verified", "relook")             # marks on the current take (they change its outcome)
STOCK = ("verified", "scripted", "rendered")              # on the way to a slot, not placed yet
CHECK_SURE = ("confirmed", "corrected")                   # the 10-20 check stands behind the expected answer
CHECK_OPEN = ("doubtful", "unknown")                      # "the recording decides"
GAMES = ("GTA 5", "GTA 6")
OFFSETS = (-3.0, -1.5, 0.0, 1.0, 2.5, 5.0)                # the frames around a mark (seconds)
VIDEO_EXT = (".mp4", ".mov", ".webm")
TAKE_NAME = re.compile(r"^(?P<id>T\d{3})(?:-(?P<take>\d+))?(?:\s+[^/]*)?\.(?P<ext>mp4|mov|webm)$", re.I)
DUPLICATE = 0.85                                          # difflib ratio of two questions' content words
SETTLE_S = 60                                             # an inbox file unchanged this long is whole
MIN_TAKE_S = 10.0
HDR_TRANSFERS = ("smpte2084", "arib-std-b67")
DEFAULTS: Dict[str, object] = {
    "bank": "Publish/tests.json",
    "capture_root": "~/Movies/Lab Capture",
    "capture_link": "Capture",
    "obs": "",
    "capture_hours": {"from": "06:15", "until": "20:45"},
    "take_warn_minutes": 20,
    "take_stop_minutes": 30,
    "free_gb": {"refuse": 60, "warn": 150},
    "dropped_frames": {"warn": 0.005, "redo": 0.03},
    "game_by_date": {"before": "GTA 5", "from": "2026-11-19", "after": "GTA 6"},
    "post_games": [],
    "deal": {"extra": 2, "batch_by": "area", "max_per_kind": 2},
    "variety": {"structures": ["ask-show", "result-first", "myth-check", "three-tries", "versus", "secret-tour"],
                "structure_share": 0.34, "area_share": 0.34, "neighbours_differ": True, "opener_days": 14,
                "verdict_similar": 0.8, "verdict_window": 30},
    "same_day_lead_hours": 2,
    "render_timeout_minutes": 45,
    "unconfirmed_stale_days": 7,
    "inbox_extra": [],
}
# the skill's stock comment questions (gtacopy.default_question): a Lab clip's question is never one of them
STOCK_QUESTIONS = {"would you have made it out?", "same reaction, or not?", "did you catch it the first time?",
                   "which one is your number one?", "does this change how you will play it?",
                   "gta 5 or gta 6, which one wins this?", "what are you doing on day one?",
                   "what did you see that i missed?"}
# words a Lab script never says (the safety rules): no glitches or exploits, no mods or PC trainers
BANNED = re.compile(r"\b(glitch(?:es|ed)?|exploits?|money glitch|unlimited money|duplication|dupe|infinite|"
                    r"mods?|modded|modding|trainers?|menu)\b", re.I)
GTA6_WORDS = re.compile(r"\bgta ?(?:6|vi)\b|\bgrand theft auto ?(?:6|vi)\b|\bleonida\b|\blucia\b|\bjason\b", re.I)
GAME_LABEL = {"GTA 5": re.compile(r"\bgta ?(?:5|v)\b|\bgrand theft auto ?(?:5|v)\b", re.I),
              "GTA 6": re.compile(r"\bgta ?(?:6|vi)\b|\bgrand theft auto ?(?:6|vi)\b", re.I)}
DASHES = re.compile(r"\s[-\u2013\u2014]\s|[\u2013\u2014]")   # a dash as punctuation, not a hyphen


class LabError(Exception):
    """A refusal said to a person (nothing was written)."""


# ------------------------------------------------------------------------------------------------------ settings
def is_lab(acct: C.Account) -> bool:
    return acct.maker == MAKER


def lab_accounts(cfg: C.Config, here: bool = True) -> List[C.Account]:
    """Every account whose maker is "lab", switched on or not (recording and scripting build stock before switch-on);
    with `here`, only the ones this machine runs."""
    every = cfg.all_accounts or cfg.accounts
    return [a for a in every.values() if is_lab(a) and (not here or a.machine == cfg.machine)]


def settings(acct: C.Account) -> dict:
    """The account's "lab" block over DEFAULTS (one level of dicts merged)."""
    own = (acct.raw or {}).get("lab") or {}
    out = copy.deepcopy(DEFAULTS)
    for k, v in own.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = dict(out[k], **v)
        else:
            out[k] = v
    if not out.get("obs"):
        out["obs"] = f"~/.config/clip-factory/{acct.slug}/obs.json"
    return out


def bank_path(acct: C.Account) -> Path:
    return acct.account_dir / str(settings(acct)["bank"])


def obs_path(acct: C.Account) -> Path:
    return C.expand(str(settings(acct)["obs"]))


def icloud_roots() -> List[Path]:
    """The folders iCloud syncs here (FXICloudDriveDesktop: the Desktop and Documents)."""
    home = Path(os.path.expanduser("~"))
    return [home / "Desktop", home / "Documents", home / "Library" / "Mobile Documents"]


def in_icloud(p: Path) -> bool:
    try:
        real = Path(os.path.realpath(p))
    except (OSError, ValueError):
        real = Path(p)
    for top in icloud_roots():
        try:
            t = Path(os.path.realpath(top))
        except (OSError, ValueError):
            t = top
        if real == t or t in real.parents:
            return True
    return False


def _scratch(p: Path) -> bool:
    """Is p inside a throwaway folder (the system's temp folders)? A test or an offline run writes only there."""
    try:
        real = Path(os.path.realpath(p))
    except (OSError, ValueError):
        return False
    tops = {Path(os.path.realpath(tempfile.gettempdir())), Path("/private/tmp"), Path("/private/var/folders")}
    return any(real == t or t in real.parents for t in tops)


def guard_real(p: Path, what: str) -> None:
    """A test or an offline run never writes the real capture root or the real bank (livestate.py guards state/
    only): it works on a throwaway copy of the account."""
    from . import livestate
    if livestate.test_run() and not _scratch(p):
        raise LabError(f"refused: a test or an offline run never touches the real {what} ({p})")


def capture_root(acct: C.Account, check: bool = True) -> Path:
    """Where the raw takes live: outside the Desktop (iCloud syncs it, and 4K HEVC runs 18 to 27 GB an hour)."""
    root = C.expand(str(settings(acct)["capture_root"]))
    if check and in_icloud(root):
        raise LabError(f"the capture root {root} is inside iCloud (the Desktop or Documents): 4K takes would upload "
                       f"there. Put lab.capture_root under ~/Movies (config/accounts.json)")
    if check:
        guard_real(root, "capture root")
    return root


def lab_state(acct: C.Account) -> Path:
    return C.STATE / acct.slug / "lab"


def _hm(t: datetime) -> str:
    return t.strftime("%H:%M")


def iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def log(acct: C.Account, what: str, **kw) -> None:
    """One line per record, mark, stop, outcome, script, redo and place in state/<account>/lab/log.jsonl."""
    p = lab_state(acct) / "log.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(dict({"at": B.utcnow(), "what": what}, **kw), sort_keys=True) + "\n")


def game_on(acct: C.Account, day: date) -> str:
    """The game a test is recorded in on `day` (lab.game_by_date): GTA 5 before the launch date, GTA 6 from it."""
    g = settings(acct)["game_by_date"]
    try:
        turn = date.fromisoformat(str(g.get("from")))
    except (TypeError, ValueError):
        return str(g.get("before") or "GTA 5")
    return str(g.get("after") or "GTA 6") if day >= turn else str(g.get("before") or "GTA 5")


def launch_day(acct: C.Account) -> Optional[date]:
    try:
        return date.fromisoformat(str(settings(acct)["game_by_date"].get("from")))
    except (TypeError, ValueError):
        return None


def post_games(acct: C.Account, day: date) -> List[str]:
    """The games whose tests fill `day`'s slots, first first (lab.post_games, a dated rule like "volume"): GTA 5 only
    before the launch; GTA 6 first from it, GTA 5 filling what no GTA 6 test can (still labelled GTA 5)."""
    for r in settings(acct).get("post_games") or []:
        lo, hi = r.get("from"), r.get("until")
        try:
            if (not lo or day >= date.fromisoformat(lo)) and (not hi or day <= date.fromisoformat(hi)):
                return [str(g) for g in r.get("games") or []]
        except (TypeError, ValueError):
            continue
    return [game_on(acct, day)]


def opens(acct: C.Account, t: dict, day: date) -> str:
    """'' when the test may be recorded on `day`, else why not (its game's date, its not_before)."""
    if t.get("game") == "GTA 6" and game_on(acct, day) != "GTA 6":
        return f"GTA 6 tests open on {launch_day(acct)} (lab.game_by_date)"
    nb = t.get("not_before")
    if nb:
        try:
            if day < date.fromisoformat(str(nb)):
                return f"{t['id']} opens on {nb} (not_before)"
        except ValueError:
            return f"{t['id']} has a not_before that is not a date ({nb!r})"
    return ""


# ------------------------------------------------------------------------------------------------------ the bank
def empty_bank() -> dict:
    return {"_what": "The Lab test bank. One row per question. Status moves todo -> recorded -> verified -> "
                     "scripted -> rendered -> placed -> posted; held, relook, redo and dropped are side states. "
                     "Write it with factory.py lab, never by hand.",
            "version": 1, "next_id": 1, "tests": []}


def forks(path: Path) -> List[Path]:
    """iCloud's copies of the bank ("tests 2.json" ... "tests 9.json") beside it."""
    return [path.with_name(f"{path.stem} {n}{path.suffix}") for n in range(2, 10)
            if path.with_name(f"{path.stem} {n}{path.suffix}").exists()]


def bank_read(acct: C.Account) -> dict:
    """The bank (an empty one when there is none yet). Refused while iCloud has made a fork of it: which copy holds
    Andrew's last outcome is not known."""
    p = bank_path(acct)
    fk = forks(p)
    if fk:
        raise LabError(f"iCloud made a copy of the bank ({fk[0].name} beside {p.name}): the factory reads neither "
                       f"until one is left. Compare them, keep the right one as {p.name}, move the other away")
    if not p.exists():
        return empty_bank()
    try:
        data = json.loads(p.read_text())
    except ValueError as e:
        raise LabError(f"{p} is not readable JSON ({e}): restore it from the account's Time Machine copy") from None
    if not isinstance(data, dict) or not isinstance(data.get("tests"), list):
        raise LabError(f"{p} has no tests list")
    return data


@contextlib.contextmanager
def bank_edit(acct: C.Account) -> Iterator[dict]:
    """Lock (tests.json.lock beside it), read, yield for changes, check, write a temp file, os.replace. A change that
    would leave the bank with a new problem (bank_problems) is refused and nothing is written."""
    p = bank_path(acct)
    guard_real(p, "test bank")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(p.name + ".lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        bank = bank_read(acct)
        before = set(bank_problems(bank))
        yield bank
        new = [x for x in bank_problems(bank) if x not in before]
        if new:
            raise LabError("not saved: " + "; ".join(new[:3]))
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(bank, indent=1, ensure_ascii=False) + "\n")
        os.replace(tmp, p)


STOP_WORDS = {"a", "an", "the", "in", "on", "at", "of", "to", "if", "you", "your", "do", "does", "did", "can",
              "could", "what", "really", "happens", "happen", "is", "are", "there", "it", "its", "and", "or",
              "with", "for", "from", "by", "when", "while", "who", "how", "gta", "5", "6", "v", "vi", "grand",
              "theft", "auto", "will", "would", "get", "secret", "hidden"}


def content_words(text: str) -> List[str]:
    """A question's words that carry its meaning: lowercase, no game label, no stop words, a plural's s off."""
    words = re.findall(r"[a-z0-9']+", str(text or "").lower())
    out = []
    for w in words:
        w = w.strip("'")
        if w.endswith("'s"):
            w = w[:-2]
        if not w or w in STOP_WORDS:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def similar(a: str, b: str) -> float:
    """How alike two questions are: difflib's ratio over their content words, sorted (word order does not hide a
    copy; one changed word in a short question is not one)."""
    x, y = sorted(content_words(a)), sorted(content_words(b))
    if not x or not y:
        return 0.0
    return difflib.SequenceMatcher(None, x, y).ratio()


def bank_problems(bank: dict) -> List[str]:
    """What is wrong with the bank: ids, statuses, games, dates, holds, and two questions of one game alike."""
    out: List[str] = []
    seen: Dict[str, int] = {}
    tests = bank.get("tests") or []
    top = 0
    for t in tests:
        tid = str(t.get("id") or "")
        if not re.fullmatch(r"T\d{3}", tid):
            out.append(f"a test has the id {tid!r} (T001 ... T999)")
            continue
        seen[tid] = seen.get(tid, 0) + 1
        top = max(top, int(tid[1:]))
        if t.get("status") not in STATUSES:
            out.append(f"{tid}: status {t.get('status')!r} is not one of {', '.join(STATUSES)}")
        if t.get("game") not in GAMES:
            out.append(f"{tid}: game {t.get('game')!r} (GTA 5 or GTA 6)")
        if not str(t.get("question") or "").strip():
            out.append(f"{tid}: no question")
        if t.get("not_before"):
            try:
                date.fromisoformat(str(t["not_before"]))
            except ValueError:
                out.append(f"{tid}: not_before {t['not_before']!r} is not a date")
        if (t.get("status") == "held") != bool(t.get("hold")):
            out.append(f"{tid}: " + ("held with no hold" if t.get("status") == "held" else "a hold but not held"))
    out += [f"{tid} is in the bank {n} times" for tid, n in seen.items() if n > 1]
    if int(bank.get("next_id") or 0) <= top:
        out.append(f"next_id {bank.get('next_id')} is not past T{top:03d}")
    live = [t for t in tests if t.get("status") != "dropped" and t.get("question")]
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if a.get("game") == b.get("game") and similar(a["question"], b["question"]) >= DUPLICATE:
                out.append(f"{a.get('id')} and {b.get('id')} ask the same question")
    return out


def find(bank: dict, tid: str) -> dict:
    tid = str(tid or "").strip().upper()
    t = next((x for x in bank.get("tests") or [] if x.get("id") == tid), None)
    if t is None:
        raise LabError(f"no test {tid!r} in the bank")
    return t


def note(t: dict, what: str, now: Optional[datetime] = None) -> None:
    t.setdefault("history", []).append({"at": iso(now) if now else B.utcnow(), "what": what[:500]})


def set_status(t: dict, status: str, what: str, now: Optional[datetime] = None) -> None:
    if status not in STATUSES:
        raise LabError(f"{status!r} is not a Lab status")
    was = t.get("status")
    t["status"] = status
    note(t, f"{was} -> {status}: {what}" if was != status else what, now)


# the one-time import ---------------------------------------------------------------------------------------------
# GTA 5's phone cheats the research names, by number (the setup says each one too; the card shows it to dial)
CHEATS = {"1-999-633-7623": "Sanchez", "1-999-289-9633": "Buzzard", "1-999-227-678-676": "Stunt plane",
          "1-999-467-8648": "Super Jump", "1-999-384-48483": "Raise Wanted Level", "1-999-727-4348": "Rapid GT",
          "1-999-266-38": "Comet", "1-999-872-7433": "Trashmaster", "1-999-356-2837": "Moon Gravity",
          "1-999-759-3255": "Skyfall", "1-999-468-42637": "Explosive Melee", "1-999-759-3483": "Parachute",
          "1-999-724-6545537": "Invincibility", "1-999-769-3787": "Recharge Ability"}
PHONE = re.compile(r"\b1-999-[\d-]*\d\b")
STEP = re.compile(r"(?:^|(?<=\s))(\d{1,2})\.\s+")
# the 10-20 check found the graves test wrong: eight bodies on the sea bed around the North Point islands
CORRECTIONS = {
    "The secret underwater graves in GTA 5": {
        "answer": "Eight bodies on the sea bed around the North Point islands, north of Paleto Bay.",
        "setup": {"Go to the small islands north of Mount Chiliad.": "Go to the North Point islands, north of Paleto Bay.",
                  "Record the five wrapped bodies at a respectful distance.":
                      "Record the wrapped bodies at a respectful distance (gta.wiki counts eight)."},
        "check_note": "The research said five, north of Mount Chiliad; the 10-20 check corrected it to eight bodies "
                      "on the sea bed around the North Point islands, north of Paleto Bay (gta.wiki Merle_Abrahams, "
                      "GTA BOOM)."},
}
AREAS = (
    ("Fort Zancudo", r"zancudo"),
    ("Paleto Bay & the sea", r"paleto|north point|underwater|bottom of the sea|sea ?bed|shipwreck|\bdive\b|dive "
                             r"to|scuba|shark|dinghy|hold their breath|into the water"),
    ("Sandy Shores & Chiliad", r"chiliad|sandy shores|blaine|bolingbroke|prison|cult|altruist|cable car|mountain "
                               r"lion|cougar|mount gordo|alamo|senora|serial killer|raton|mural|jetpack|stunt plane"),
    ("Los Santos", r"los santos|mission row|kortz|grove street|vinewood|airport|runway|del perro|cemetery|pleasure "
                   r"pier|ferris|naval port|merryweather|strawberry|fridgit|pacific bluffs|rockford|downtown|"
                   r"franklin's house|boulevard|busy street|24/7|ltd store|fire truck|nothing here"),
)
WANTED = r"\bcops?\b|police|wanted|\bstars?\b|zancudo|prison|runway|kortz|merryweather|surrender"


def kind_of(question: str, setup: str) -> str:
    """A test's kind, for the day's mix (deal.max_per_kind): world-secret (the question asks after a secret),
    wildlife (an animal, the plants), wanted (the cops, the stars, a restricted area), npc (how people react),
    character (one of the three against another), else physics (cheats, trains, falls)."""
    q, both = question, question + " " + setup
    if re.search(r"\bsecret\b|\bhidden\b|jetpack|ghost|nothing here", q, re.I):
        return "world-secret"
    if re.search(r"\bplants?\b|shark|mountain lion|cougar|seagull|\bbird\b|\bdog\b|\bcow\b|chicken|alligator|"
                 r"\bgator\b|manatee|\bzoo\b", both, re.I):
        return "wildlife"
    if re.search(WANTED, both, re.I):
        return "wanted"
    if re.search(r"shopkeeper|pull up|grove street|shout|drivers|witness|social media|viral", q, re.I):
        return "npc"
    if re.search(r"michael|franklin|trevor", q, re.I):
        return "character"
    return "physics"


def hud_for(kind: str, text: str) -> str:
    """Wanted tests keep the HUD on: the stars are the evidence (hud.py reads them). Every other test: off."""
    return "on" if kind == "wanted" or re.search(r"\bstars?\b|wanted level", text, re.I) else "off"


def split_steps(text: str) -> List[str]:
    """"1. Any character. 2. Spawn ..." -> ["Any character.", "Spawn ..."]: only numbers in order count."""
    text = str(text or "").strip()
    want, cuts = 1, []
    for m in STEP.finditer(text):
        if int(m.group(1)) == want:
            cuts.append(m)
            want += 1
    if len(cuts) < 2:
        return [text] if text else []
    return [text[a.end():(cuts[i + 1].start() if i + 1 < len(cuts) else len(text))].strip()
            for i, a in enumerate(cuts)]


def _first(rules, text: str, default: str) -> str:
    return next((name for name, pat in rules if re.search(pat, text, re.I)), default)


def import_bank(research: dict, check: dict, now: Optional[datetime] = None,
                launch: str = "2026-11-19") -> dict:
    """The bank from the 10-20 research (p4_research_test-list: 60 GTA 5, 25 GTA 6) with its check merged in
    (p4_check_test-list: confirmed, doubtful = "the recording decides", one wrong, corrected here). T001-T060 are
    GTA 5 in the research's order, T061-T085 GTA 6 (not_before the launch). A test the research marks Caution (the
    six plant tests, hitting pedestrians) starts held, for Andrew's Allow."""
    stamp = iso(now) if now else B.utcnow()
    verdicts = {str(c.get("question") or "").strip(): c for c in (check.get("checked") or [])}
    bank = empty_bank()
    rows = [("GTA 5", r) for r in research.get("gta5") or []] + [("GTA 6", r) for r in research.get("gta6") or []]
    for n, (game, r) in enumerate(rows, start=1):
        q = str(r.get("question") or "").strip()
        fix = CORRECTIONS.get(q, {})
        steps = split_steps(r.get("setup") or "")
        steps = [fix.get("setup", {}).get(s, s) for s in steps]
        text = " ".join([q] + steps + [str(r.get("source") or "")])
        cheats, seen_n = [], set()
        for ph in PHONE.findall(" ".join(steps)):
            if ph not in seen_n:
                seen_n.add(ph)
                cheats.append({"what": CHEATS.get(ph, "phone cheat"), "phone": ph})
        v = verdicts.get(q) or {}
        if game == "GTA 6":
            check_word, check_note = "unknown", "The recording decides."
        else:
            check_word = {"confirmed": "confirmed", "doubtful": "doubtful", "wrong": "corrected"}.get(
                str(v.get("verdict") or ""), "unknown")
            check_note = fix.get("check_note") or str(v.get("note") or "")
            if check_word == "doubtful":
                check_note = "The recording decides. " + check_note
        kind = kind_of(q, " ".join(steps))
        safety = str(r.get("safety") or "").strip()
        caution = safety.lower().startswith("caution")
        t = {
            "id": f"T{n:03d}",
            "game": game,
            "question": q,
            "kind": kind,
            "area": "launch" if game == "GTA 6" else _first(AREAS, text, "anywhere"),
            "setup": steps,
            "hud": hud_for(kind, q + " " + " ".join(steps)),
            "cheats": cheats,
            "save": "test",
            "seconds": int(r.get("seconds") or 40),
            "expected": {
                "answer": fix.get("answer") or str(r.get("answer") or ""),
                "sources": [s.strip() for s in str(r.get("source") or "").split(" ; ") if s.strip()],
                "check": check_word,
                "check_note": check_note,
            },
            "safety": {"level": "caution" if caution else "ok", "notes": [safety] if safety else []},
            "hold": None,
            "not_before": launch if game == "GTA 6" else None,
            "viral": int(r.get("viral") or 1),
            "fresh": bool(r.get("fresh")),
            "status": "todo",
            "takes": [],
            "take": None,
            "outcome": None,
            "script": None,
            "job": None,
            "placed": None,
            "declined": [],
            "render_refusals": 0,
            "history": [{"at": stamp, "what": "imported from the 10-20 research (p4_research_test-list, "
                                              "p4_check_test-list)"}],
        }
        if game == "GTA 6":
            t["expected"]["note"] = str(r.get("system") or "")
        if r.get("why"):
            t["demand"] = str(r["why"])
        if caution:
            why = ("the plant tests wait for Andrew's yes (the plant is an in-game hallucinogen; TikTok's "
                   "drug-reference rules not checked)" if re.search(r"\bplant\b|peyote", text, re.I) else
                   "waits for Andrew's yes: " + safety[len("caution:"):].strip())
            t["hold"] = {"why": why, "since": stamp[:10], "was": "todo"}
            t["status"] = "held"
        bank["tests"].append(t)
    bank["next_id"] = len(rows) + 1
    return bank


def add_test(acct: C.Account, question: str, game: str, kind: str = "", area: str = "", setup: Optional[list] = None,
             hud: str = "", answer: str = "", sources: Optional[list] = None, viral: int = 3, seconds: int = 40,
             by: str = "", now: Optional[datetime] = None) -> str:
    """A new question (factory.py lab bank add). Refused when it asks what a test of the same game already asks."""
    q = " ".join(str(question or "").split())
    if game not in GAMES:
        raise LabError(f"--game is GTA 5 or GTA 6, not {game!r}")
    if not q:
        raise LabError("the question is empty")
    with bank_edit(acct) as bank:
        for t in bank["tests"]:
            if t.get("game") == game and t.get("status") != "dropped" and similar(t.get("question", ""), q) >= DUPLICATE:
                raise LabError(f"Already in the bank as {t['id']}: {t['question']}")
        tid = f"T{int(bank.get('next_id') or 1):03d}"
        text = " ".join([q] + list(setup or []))
        k = kind or kind_of(q, " ".join(setup or []))
        t = {"id": tid, "game": game, "question": q, "kind": k,
             "area": area or ("launch" if game == "GTA 6" else _first(AREAS, text, "anywhere")),
             "setup": list(setup or []), "hud": hud or hud_for(k, text),
             "cheats": [{"what": CHEATS.get(p, "phone cheat"), "phone": p} for p in dict.fromkeys(PHONE.findall(text))],
             "save": "test", "seconds": int(seconds), "viral": int(viral), "fresh": True,
             "expected": {"answer": answer, "sources": list(sources or []), "check": "unknown",
                          "check_note": "The recording decides."},
             "safety": {"level": "ok", "notes": []}, "hold": None,
             "not_before": str(launch_day(acct)) if game == "GTA 6" and launch_day(acct) else None,
             "status": "todo", "takes": [], "take": None, "outcome": None, "script": None, "job": None,
             "placed": None, "declined": [], "render_refusals": 0, "history": []}
        note(t, f"added by {by or 'factory.py lab bank add'}", now)
        bank["tests"].append(t)
        bank["next_id"] = int(tid[1:]) + 1
    log(acct, "bank-add", test=tid, question=q)
    return tid


def hold_test(acct: C.Account, tid: str, why: str, now: Optional[datetime] = None) -> str:
    if not str(why or "").strip():
        raise LabError("a hold needs --why")
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        if t["status"] in ("placed", "posted", "dropped"):
            raise LabError(f"{t['id']} is {t['status']}: nothing to hold")
        t["hold"] = {"why": why.strip()[:300], "since": (now or datetime.now()).date().isoformat(), "was": t["status"]}
        set_status(t, "held", f"held: {why.strip()[:300]}", now)
    log(acct, "hold", test=t["id"], why=why)
    return f"{t['id']} held: {why.strip()}"


def _unheld_status(t: dict) -> str:
    """What a held test goes back to: verified when its current take has a confirmed outcome, recorded when it has a
    take, else todo."""
    if t.get("take") and (t.get("outcome") or {}).get("kind") in ("as_expected", "different"):
        return "verified"
    if t.get("take"):
        return "recorded"
    return "todo"


# ------------------------------------------------------------------------------------------------------ takes
def take_stem(tid: str, n: int) -> str:
    return tid if int(n) == 1 else f"{tid}-{int(n)}"


def current_take(t: dict) -> Optional[dict]:
    n = t.get("take")
    return next((x for x in t.get("takes") or [] if x.get("n") == n), None) if n else None


def read_json(p: Path) -> dict:
    try:
        data = json.loads(Path(p).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(p: Path, data: dict) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, p)


@contextlib.contextmanager
def sidecar_edit(p: Path) -> Iterator[dict]:
    """A take's sidecar, locked (the page's marks and outcome, and the checks thread, write it)."""
    p = Path(p)
    with open(p.with_name(p.name + ".lock"), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        sc = read_json(p)
        yield sc
        write_json(p, sc)


def take_file(sc: dict, sc_path: Path) -> Path:
    return Path(sc_path).parent / str(sc.get("file") or "")


def seen_key(sc: dict, sc_path: Path) -> str:
    """What the page shows of a take, as it is on disk now: the file (its size and time) and the marks. The page
    sends it back with an outcome tap, which refuses when it differs (a mark or the take changed under the page)."""
    f = take_file(sc, sc_path)
    try:
        st = os.stat(f)
        fs = [st.st_size, st.st_mtime_ns]
    except OSError:
        fs = None
    marks = [[m.get("n"), round(float(m.get("t") or 0), 2)] for m in sc.get("marks") or []]
    return hashlib.sha256(json.dumps([str(f), fs, marks], sort_keys=True).encode()).hexdigest()[:12]


def _ffprobe(path: Path, timeout: int = 60) -> dict:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-show_chapters", "-of", "json",
                            str(path)], capture_output=True, text=True, timeout=timeout)
        return json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def _rate(s: str) -> float:
    try:
        a, _, b = str(s or "0/1").partition("/")
        return round(float(a) / float(b or 1), 3) if float(b or 1) else 0.0
    except ValueError:
        return 0.0


def probe(path: Path) -> dict:
    """The take as the factory checks it: size, fps, codec, transfer (HDR or not), length, creation time, sound, chapters."""
    d = _ffprobe(path)
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = d.get("format") or {}
    if v is None:
        return {"video": False}
    transfer = str(v.get("color_transfer") or "")
    try:
        dur = round(float(fmt.get("duration") or v.get("duration") or 0), 2)
    except ValueError:
        dur = 0.0
    tags = dict(fmt.get("tags") or {})
    tags.update({k: val for k, val in (v.get("tags") or {}).items() if k == "creation_time" and "creation_time" not in tags})
    chapters = []
    for ch in d.get("chapters") or []:
        try:
            chapters.append({"t": round(float(ch.get("start_time") or 0), 2),
                             "title": str((ch.get("tags") or {}).get("title") or "")})
        except ValueError:
            continue
    return {"video": True, "w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
            "fps": _rate(v.get("avg_frame_rate") or v.get("r_frame_rate")), "codec": str(v.get("codec_name") or ""),
            "transfer": transfer, "hdr": transfer in HDR_TRANSFERS, "duration_s": dur,
            "creation_time": str(tags.get("creation_time") or ""), "audio": a is not None, "chapters": chapters}


def black_spans(path: Path, timeout: int = 1800) -> Optional[List[List[float]]]:
    """Spans of black picture (the card pulled mid-take), from a small decode; None when ffmpeg could not read it."""
    try:
        r = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-hwaccel", "auto", "-i", str(path), "-vf",
                            "scale=320:-2,blackdetect=d=0.5:pix_th=0.10", "-an", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    out = []
    for m in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)", r.stderr or ""):
        out.append([round(float(m.group(1)), 2), round(float(m.group(2)), 2)])
    return out if r.returncode == 0 or out else None


def audio_rms(path: Path, timeout: int = 900) -> Optional[float]:
    """The take's mean sound level in dB (volumedetect); None with no sound."""
    try:
        r = subprocess.run(["ffmpeg", "-nostats", "-hide_banner", "-i", str(path), "-vn", "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", r.stderr or "")
    if not m:
        return None
    return -120.0 if m.group(1) == "-inf" else float(m.group(1))


def sha256_path(p: Path) -> str:
    return B.sha256_file(Path(p))


_CHECKING: Dict[str, threading.Thread] = {}


def finish_checks(sc_path: Path) -> None:
    """The slow part of a take's checks (the sha256, the black spans, the sound), written into its sidecar."""
    sc_path = Path(sc_path)
    sc = read_json(sc_path)
    f = take_file(sc, sc_path)
    if not f.is_file():
        return
    sha = sc.get("sha256") or sha256_path(f)
    spans = black_spans(f)
    rms = audio_rms(f) if (sc.get("probe") or {}).get("audio") else None
    with sidecar_edit(sc_path) as s2:
        s2["sha256"] = sha
        ch = dict(s2.get("checks") or {})
        ch.pop("pending", None)
        ch["black_spans"] = spans if spans is not None else []
        if spans is None:
            ch["black_note"] = "blackdetect could not read the take"
        ch["audio_rms_dbfs"] = rms
        s2["checks"] = ch
        note(s2, "checks done (sha256, black spans, sound)")


def start_checks(sc_path: Path, background: bool = True) -> None:
    """finish_checks in a thread (the review page answers at once), or now."""
    key = str(sc_path)
    th = _CHECKING.get(key)
    if th is not None and th.is_alive():
        return
    if not background:
        finish_checks(sc_path)
        return
    th = threading.Thread(target=finish_checks, args=(sc_path,), daemon=True, name=f"lab-checks {Path(key).name}")
    _CHECKING[key] = th
    th.start()


def checks_pending(sc: dict) -> bool:
    return bool((sc.get("checks") or {}).get("pending")) or not sc.get("sha256")


# ------------------------------------------------------------------------------------------------------ capture
def setup_folders(acct: C.Account) -> List[str]:
    """The capture root (outside iCloud), its _obs, inbox and inbox/Refused, and the account folder's Capture link to
    it. Idempotent; never moves or deletes anything."""
    root = capture_root(acct)
    out = []
    for d in (root, root / "_obs", root / "inbox", root / "inbox" / "Refused"):
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            out.append(f"  made {d}")
    link = acct.account_dir / str(settings(acct)["capture_link"])
    if acct.account_dir.is_dir():
        if link.is_symlink():
            if Path(os.path.realpath(link)) != Path(os.path.realpath(root)):
                out.append(f"  {link} is a link to {os.readlink(link)}, not {root}: left as it is")
        elif link.exists():
            out.append(f"  {link} is a real folder, not a link to {root}: left as it is (takes there sync to iCloud)")
        else:
            link.symlink_to(root)
            out.append(f"  linked {link} -> {root}")
    else:
        out.append(f"  no account folder {acct.account_dir} yet: the Capture link waits for it")
    return out or [f"  {root} and its folders are in place"]


def _rec_path(acct: C.Account) -> Path:
    return lab_state(acct) / "recording.json"


@contextlib.contextmanager
def _rec_lock(acct: C.Account) -> Iterator[None]:
    p = lab_state(acct) / "recording.lock"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        yield


def recording(acct: C.Account) -> Optional[dict]:
    """The take being recorded now (state/<account>/lab/recording.json), or None."""
    p = _rec_path(acct)
    return read_json(p) or None if p.exists() else None


def free_gb(p: Path) -> float:
    p = Path(p)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return round(shutil.disk_usage(p).free / 1e9, 1)
    except OSError:
        return 0.0


def _obs(acct: C.Account):
    from . import obs as O
    return O.Obs.from_file(obs_path(acct))


def _caffeinate(seconds: int) -> Optional[int]:
    """Keep the Mac awake for the take (never in a test or an offline run)."""
    from . import net
    if net.why_not() in ("offline", "a test run"):
        return None
    try:
        p = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-t", str(int(seconds))], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return p.pid
    except OSError:
        return None


def _kill(pid: object) -> None:
    try:
        n = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return
    if n > 0:
        with contextlib.suppress(OSError):
            os.kill(n, signal.SIGTERM)


def write_health(acct: C.Account, ok: bool, why: str, **kw) -> None:
    """state/<account>/lab/obs-health.json: the last OBS check (ok / why / when). Never the password."""
    write_json(lab_state(acct) / "obs-health.json", dict({"ok": ok, "why": why[:300], "at": B.utcnow()}, **kw))


def health(acct: C.Account) -> dict:
    return read_json(lab_state(acct) / "obs-health.json")


def obs_check(acct: C.Account, deep: bool = False) -> dict:
    """OBS as the status strip and `lab obs-check` see it: reachable, its version, the card's picture and sound, the
    record directory, the video settings. Writes obs-health.json."""
    from . import obs as O
    out: dict = {"ok": False}
    try:
        with _obs(acct) as ob:
            v = ob.version()
            out.update(ok=True, why="connected", obs=str(v.get("obsVersion") or ""),
                       ws=str(v.get("obsWebSocketVersion") or ""))
            try:
                pic = ob.screenshot_stats(ob.video_input)
                out["picture"], out["picture_words"] = O.picture_words(pic)
                out["luma"] = pic.get("luma")
            except O.OBSError as e:
                out["picture"], out["picture_words"] = "missing", f"no picture source {ob.video_input!r} ({e})"
            if deep:
                lvl = ob.audio_level(ob.audio_input)
                out["sound_db"] = lvl
                out["video"] = ob.video_settings()
                out["record_dir"] = ob.record_dir()
                out["recording"] = bool(ob.record_status().get("outputActive"))
    except O.AuthFailed as e:
        out.update(ok=False, why=str(e), kind="password")
    except O.NotReachable as e:
        out.update(ok=False, why=str(e), kind="closed")
    except O.OBSError as e:
        out.update(ok=False, why=str(e), kind="error")
    write_health(acct, bool(out.get("ok")), str(out.get("why") or ""),
                 **{k: v for k, v in out.items() if k in ("obs", "ws", "picture", "luma", "kind")})
    return out


def record(cfg: C.Config, acct: C.Account, tid: str, by: str = "", cheats_ok: bool = False,
           now: Optional[datetime] = None) -> str:
    """Record: the capture hours, the game date, the hold, free space, OBS, the card's picture and sound, then
    OBS records into the capture root's _obs, the Mac kept awake, the take written in recording.json."""
    from . import obs as O
    now = now or cfg.now()
    st = settings(acct)
    hours = st["capture_hours"]
    if not (str(hours.get("from")) <= _hm(now) < str(hours.get("until"))):
        raise LabError(f"Recording is open {hours.get('from')} to {hours.get('until')} (lab.capture_hours): it "
                       f"stays clear of the night shift")
    bank = bank_read(acct)
    t = find(bank, tid)
    if t["status"] == "held":
        raise LabError(f"{t['id']} is held ({(t.get('hold') or {}).get('why')}): Allow it on the Lab tab first")
    if t["status"] not in RECORDABLE:
        raise LabError(f"{t['id']} is {t['status']}: a new take is only for a test not scripted yet (a new angle is a "
                       f"new test)")
    why = opens(acct, t, now.date())
    if why:
        raise LabError(why)
    if t.get("cheats") and not cheats_ok:
        raise LabError(f"{t['id']} dials console cheats ({', '.join(c['what'] for c in t['cheats'])}): load the "
                       f"dedicated test save first (cheats switch off trophies for the session), then press Record "
                       f"again and say it is loaded")
    root = capture_root(acct)
    setup_folders(acct)
    gb = free_gb(root)
    if gb < float(st["free_gb"]["refuse"]):
        raise LabError(f"Only {gb:.0f} GB free on the capture volume (Record needs {st['free_gb']['refuse']}): "
                       f"factory.py lab tidy {acct.slug} --dry-run lists the spent takes")
    warn: List[str] = []
    if gb < float(st["free_gb"]["warn"]):
        warn.append(f"{gb:.0f} GB free")
    with _rec_lock(acct):
        rec = recording(acct)
        if rec:
            raise LabError(f"OBS is already recording ({rec.get('test')} since {str(rec.get('started'))[11:16]})")
        try:
            with _obs(acct) as ob:
                if ob.record_status().get("outputActive"):
                    raise LabError("OBS is already recording (started in OBS itself): stop it there, then find the "
                                   "take under Untagged")
                pic = ob.screenshot_stats(ob.video_input)
                kind, words = O.picture_words(pic)
                if kind == "black":
                    raise LabError(words)
                if kind == "washed":
                    warn.append(words)
                lvl = ob.audio_level(ob.audio_input)
                if lvl is None or lvl < -60:
                    warn.append("no game sound from the card")
                ob.set_record_dir(root / "_obs")
                ob.start_record()
        except O.OBSError as e:
            write_health(acct, False, str(e))
            raise LabError(str(e)) from None
        n = len(t.get("takes") or []) + 1
        rec = {"test": t["id"], "take": n, "capture_date": now.date().isoformat(), "started": iso(now),
               "started_ts": time.time(), "obs_dir": str(root / "_obs"), "by": by or "",
               "caffeinate_pid": _caffeinate(int(st["take_stop_minutes"]) * 60 + 300), "marks": []}
        write_json(_rec_path(acct), rec)
    write_health(acct, True, "recording")
    log(acct, "record", test=t["id"], take=n, by=by)
    return f"Recording {t['id']} (take {n})." + (f" Note: {'; '.join(warn)}." if warn else "")


def _clock(sec: float) -> str:
    s = int(sec)
    return f"{s // 60}:{s % 60:02d}"


def mark(cfg: C.Config, acct: C.Account, tid: str, at: Optional[float] = None, label: str = "",
         now: Optional[datetime] = None) -> str:
    """A mark: during the take, at OBS's own record time (and an OBS chapter); after it, at the player's time on the
    take, which takes back any outcome given before (it is confirmed again on what the page shows now)."""
    from . import obs as O
    tid = str(tid or "").upper()
    label = " ".join(str(label or "").split())[:80]
    with _rec_lock(acct):
        rec = recording(acct)
        if rec and rec.get("test") == tid:
            try:
                with _obs(acct) as ob:
                    secs = float(ob.record_status().get("outputDuration") or 0) / 1000.0
                    n = len(rec.get("marks") or []) + 1
                    ob.chapter(f"{tid} mark {n}")
            except O.OBSError as e:
                raise LabError(str(e)) from None
            rec.setdefault("marks", []).append({"n": n, "t": round(secs, 2), "label": label, "by": "page-live"})
            write_json(_rec_path(acct), rec)
            log(acct, "mark", test=tid, n=n, t=round(secs, 2), live=True)
            return f"Mark {n} at {_clock(secs)}."
    if at is None:
        raise LabError("Mark here needs the player's time: play the take, pause on the moment, then press it")
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        if t["status"] not in MARKABLE:
            raise LabError(f"{t['id']} is {t['status']}: its marks are fixed now")
        tk = current_take(t)
        if not tk:
            raise LabError(f"{t['id']} has no take to mark")
        with sidecar_edit(Path(tk["sidecar"])) as sc:
            dur = float((sc.get("probe") or {}).get("duration_s") or 0)
            at = round(float(at), 2)
            if not 0 <= at <= dur + 0.05:
                raise LabError(f"{_clock(at)} is outside the take (it runs {_clock(dur)})")
            n = max([int(m.get("n") or 0) for m in sc.get("marks") or []] + [0]) + 1
            sc.setdefault("marks", []).append({"n": n, "t": at, "label": label, "by": "page-after"})
            cleared = _clear_outcome(sc, t, f"mark {n} added", now)
    log(acct, "mark", test=tid, n=n, t=at, live=False)
    return f"Mark {n} at {_clock(at)}." + (" The outcome was taken back: confirm it again." if cleared else "")


def _clear_outcome(sc: dict, t: dict, why: str, now: Optional[datetime]) -> bool:
    """A take whose marks changed: its outcome no longer stands (kept as outcome_before)."""
    if not sc.get("outcome"):
        return False
    sc.setdefault("outcome_before", []).append(dict(sc["outcome"], cleared=why, cleared_at=B.utcnow()))
    sc["outcome"] = None
    t["outcome"] = None
    if t.get("status") in ("verified", "relook"):
        set_status(t, "recorded", f"{why}: the outcome is confirmed again", now)
    return True


def unmark(cfg: C.Config, acct: C.Account, tid: str, n: int, now: Optional[datetime] = None) -> str:
    tid = str(tid or "").upper()
    with _rec_lock(acct):
        rec = recording(acct)
        if rec and rec.get("test") == tid:
            before = len(rec.get("marks") or [])
            rec["marks"] = [m for m in rec.get("marks") or [] if int(m.get("n") or 0) != int(n)]
            write_json(_rec_path(acct), rec)
            return f"Mark {n} taken out." if len(rec["marks"]) < before else f"No mark {n}."
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        if t["status"] not in MARKABLE:
            raise LabError(f"{t['id']} is {t['status']}: its marks are fixed now")
        tk = current_take(t)
        if not tk:
            raise LabError(f"{t['id']} has no take")
        with sidecar_edit(Path(tk["sidecar"])) as sc:
            before = len(sc.get("marks") or [])
            sc["marks"] = [m for m in sc.get("marks") or [] if int(m.get("n") or 0) != int(n)]
            if len(sc["marks"]) == before:
                raise LabError(f"No mark {n} on {t['id']}")
            cleared = _clear_outcome(sc, t, f"mark {n} taken out", now)
    log(acct, "unmark", test=tid, n=n)
    return f"Mark {n} taken out." + (" The outcome was taken back: confirm it again." if cleared else "")


def stop(cfg: C.Config, acct: C.Account, by: str = "", why: str = "", now: Optional[datetime] = None,
         background: bool = True) -> str:
    """Stop: OBS stops (its STOPPED event names the file), the file is renamed into <capture root>/<date>/T001.mp4
    (take 2: T001-2.mp4) with its sidecar, and the test is recorded. The sha256, the black spans and the sound are
    measured after, in a thread (the page answers at once)."""
    from . import obs as O
    now = now or cfg.now()
    with _rec_lock(acct):
        rec = recording(acct)
        if not rec:
            raise LabError("Nothing is recording.")
        path, stats, info, err = "", {}, {}, ""
        try:
            with _obs(acct) as ob:
                path = ob.stop_record()
                with contextlib.suppress(O.OBSError):
                    stats = ob.stats()
                with contextlib.suppress(O.OBSError):
                    v = ob.version()
                    info = {"version": str(v.get("obsVersion") or ""), "ws": str(v.get("obsWebSocketVersion") or "")}
        except O.OBSError as e:
            err = str(e)
        _kill(rec.get("caffeinate_pid"))
        _rec_path(acct).unlink()
    if err or not path:
        log(acct, "stop-lost", test=rec.get("test"), why=err or "no file")
        return (f"The take stopped without a file from OBS ({err or 'OBS named no file'}): it is under Untagged "
                f"when OBS saved it; say which test it is there.")
    src = Path(path)
    for _ in range(40):                       # OBS names the file before the last bytes are down
        try:
            a = src.stat().st_size
            time.sleep(0.25)
            if src.stat().st_size == a and a > 0:
                break
        except OSError:
            time.sleep(0.25)
    info.update(started=rec.get("started"), stopped=iso(now), obs_path_was=str(src), why_stopped=why or "Stop")
    sc_path, msg = file_take(cfg, acct, str(rec["test"]), src, "obs", now=now, marks=rec.get("marks") or [],
                             obs_info=info, stats=stats, strict=False, by=by)
    start_checks(sc_path, background=background)
    log(acct, "stop", test=rec.get("test"), take=rec.get("take"), file=str(sc_path.with_suffix("")), by=by, why=why)
    return msg


def watchdog(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None) -> List[str]:
    """A take forgotten while recording: stopped at take_stop_minutes (the warning at take_warn_minutes is the page's
    red timer). Run by the Lab page's live poll and the inbox job."""
    rec = recording(acct)
    if not rec:
        return []
    mins = (time.time() - float(rec.get("started_ts") or time.time())) / 60.0
    lim = float(settings(acct)["take_stop_minutes"])
    if mins < lim:
        return []
    try:
        return [stop(cfg, acct, by="the factory", why=f"stopped at {lim:.0f} minutes", now=now)]
    except LabError as e:
        return [str(e)]


def _local_date(cfg: C.Config, iso_s: str, fallback: Optional[float]) -> Optional[date]:
    for v in (iso_s,):
        if v:
            try:
                t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
                return t.astimezone(cfg.tz()).date()
            except ValueError:
                pass
    if fallback:
        return datetime.fromtimestamp(fallback, cfg.tz()).date()
    return None


def filed_sha(bank: dict, sha: str) -> str:
    """Where a take with this sha256 is filed already ('' when nowhere)."""
    for t in bank.get("tests") or []:
        for tk in t.get("takes") or []:
            if read_json(Path(tk.get("sidecar") or "/nonexistent")).get("sha256") == sha:
                return f"{t['id']} take {tk.get('n')}"
    return ""


def _merge_marks(marks: List[dict], chapters: List[dict], tid: str) -> List[dict]:
    """The take's marks: the page's, then each OBS chapter that is not one of them (Andrew's chapter hotkey)."""
    out = []
    for m in marks or []:
        out.append({"n": len(out) + 1, "t": round(float(m.get("t") or 0), 2), "label": str(m.get("label") or ""),
                    "by": str(m.get("by") or "page-live")})
    for ch in chapters or []:
        title = str(ch.get("title") or "")
        if title.upper().startswith(f"{tid} MARK"):
            continue
        tt = round(float(ch.get("t") or 0), 2)
        if tt <= 0.05 or any(abs(tt - m["t"]) < 0.25 for m in out):
            continue
        label = "" if re.fullmatch(r"(?i)(unnamed|chapter \d+|marker \d*)?", title) else title[:80]
        out.append({"n": len(out) + 1, "t": tt, "label": label, "by": "obs-chapter"})
    return out


def file_take(cfg: C.Config, acct: C.Account, tid: str, src: Path, route: str, now: Optional[datetime] = None,
              marks: Optional[List[dict]] = None, obs_info: Optional[dict] = None, stats: Optional[dict] = None,
              strict: bool = True, move: bool = True, sha: Optional[str] = None, by: str = "") -> Tuple[Path, str]:
    """A recording into its test as the next take: <capture root>/<capture date>/T001.<ext> (take N: T001-N.<ext>)
    and its sidecar. strict (the inbox, an untagged file): refused (LabError, the file left where it is) when it has
    no video, runs under 10 s, is HDR, is a GTA 6 test's from before the game's date, or is filed already. Not strict
    (an OBS take): HDR files it as redo, with the reason."""
    now = now or cfg.now()
    src = Path(src)
    tid = str(tid or "").upper()
    pr = probe(src)
    if not pr.get("video"):
        raise LabError(f"{src.name}: no video in it")
    if float(pr.get("duration_s") or 0) < MIN_TAKE_S:
        raise LabError(f"{src.name}: {pr.get('duration_s')} s, under {MIN_TAKE_S:.0f} s")
    if strict and pr.get("hdr"):
        raise LabError(f"{src.name}: HDR footage ({pr.get('transfer')}): turn HDR off on the console, record again")
    try:
        birth = getattr(src.stat(), "st_birthtime", None) or src.stat().st_mtime
    except OSError:
        birth = None
    made = _local_date(cfg, pr.get("creation_time") or "", birth) if route != "obs" else now.date()
    made = made or now.date()
    root = capture_root(acct)
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        if t["status"] in ("held", "dropped"):
            raise LabError(f"{t['id']} is {t['status']}: no take is filed under it")
        if t["status"] not in RECORDABLE:
            raise LabError(f"{t['id']} is {t['status']}: a new take is only for a test not scripted yet")
        if t.get("game") == "GTA 6" and launch_day(acct) and made < launch_day(acct):
            raise LabError(f"{src.name}: a GTA 6 test, recorded {made}, before GTA 6's {launch_day(acct)}: GTA 6 tests "
                           f"open on {launch_day(acct)} (lab.game_by_date)")
        if strict:
            sha = sha or sha256_path(src)
            where = filed_sha(bank, sha)
            if where:
                raise LabError(f"{src.name}: the same file is filed already as {where}")
        n = len(t.get("takes") or []) + 1
        stem = take_stem(t["id"], n)
        day_dir = root / made.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        dest = day_dir / f"{stem}{src.suffix.lower()}"
        sc_path = day_dir / f"{stem}.json"
        if dest.exists() or sc_path.exists():
            raise LabError(f"{dest.name} is there already in {day_dir}: nothing moved")
        if move:
            try:
                os.rename(src, dest)
            except OSError:
                shutil.move(str(src), str(dest))
        else:
            shutil.copy2(src, dest)
        st = dest.stat()
        drop = _dropped(stats or {})
        sc = {"test": t["id"], "take": n, "account": acct.slug, "game": t.get("game"),
              "capture_date": made.isoformat(), "file": dest.name, "route": route, "bytes": st.st_size,
              "mtime_ns": st.st_mtime_ns, "sha256": sha,
              "probe": {k: v for k, v in pr.items() if k not in ("chapters", "video")},
              "checks": {"pending": True, "dropped_frames": drop,
                         "obs_render_skipped": (stats or {}).get("renderSkippedFrames"),
                         "obs_output_skipped": (stats or {}).get("outputSkippedFrames")},
              "obs": dict(obs_info or {}, chapters=[c["t"] for c in pr.get("chapters") or []]) if route == "obs" else None,
              "console": str(read_settings_quiet(acct).get("console") or ""), "save": "test",
              "hud": t.get("hud") or "off",
              "marks": _merge_marks(marks or [], pr.get("chapters") or [], t["id"]),
              "outcome": None, "notes": "", "history": [{"at": iso(now), "what": f"filed from {route} ({src.name})"}]}
        write_json(sc_path, sc)
        t.setdefault("takes", []).append({"n": n, "date": made.isoformat(), "file": str(dest), "sidecar": str(sc_path),
                                          "route": route, "at": iso(now), "by": by})
        t["take"] = n
        t["outcome"] = None
        t.pop("relook", None)
        said = []
        lim = settings(acct)["dropped_frames"]
        if drop is not None and drop > float(lim["redo"]):
            said.append(f"OBS dropped {drop:.1%} of the frames: Redo suggested (was a render running?)")
        elif drop is not None and drop > float(lim["warn"]):
            said.append(f"OBS dropped {drop:.1%} of the frames")
        if pr.get("hdr"):
            set_status(t, "redo", f"take {n} is HDR footage ({pr.get('transfer')}): turn HDR off on the console, then "
                                  f"Redo", now)
            said.insert(0, "HDR footage: turn HDR off on the console, then Redo")
        else:
            set_status(t, "recorded", f"take {n} filed from {route}: mark the moment, then say what happened", now)
    log(acct, "file", test=t["id"], take=n, route=route, file=str(dest))
    return sc_path, (f"{t['id']} take {n} saved ({dest.parent.name}/{dest.name}, {_clock(float(pr.get('duration_s') or 0))})."
                     + (" " + "; ".join(said) + "." if said else " Mark the moment, then say what happened."))


def read_settings_quiet(acct: C.Account) -> dict:
    """obs.json without the password (for the sidecar's console); {} when it cannot be read."""
    from . import obs as O
    try:
        d = O.read_settings(obs_path(acct))
    except O.OBSError:
        return {}
    return {k: v for k, v in d.items() if k != "password"}


def _dropped(stats: dict) -> Optional[float]:
    shares = []
    for a, b in (("renderSkippedFrames", "renderTotalFrames"), ("outputSkippedFrames", "outputTotalFrames")):
        try:
            if float(stats.get(b) or 0) > 0:
                shares.append(float(stats.get(a) or 0) / float(stats[b]))
        except (TypeError, ValueError):
            continue
    return round(max(shares), 5) if shares else None


# ------------------------------------------------------------------------------------------------------ the inbox
def _refuse_into(p: Path, folder: Path, why: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / p.name
    k = 2
    while dest.exists():
        dest = folder / f"{p.stem} ({k}){p.suffix}"
        k += 1
    shutil.move(str(p), str(dest))
    dest.with_name(dest.name + ".txt").write_text(why.strip() + "\n")
    return dest


def inbox_scan(cfg: C.Config, acct: C.Account, now: Optional[datetime] = None, settle_s: float = SETTLE_S,
               background: bool = False) -> List[str]:
    """The fallback without OBS: a console's own recording copied into <capture root>/inbox, named by its test
    (T014.mp4 | T014.mov | T014.webm | "T014 anything.mp4" | T014-2.mp4). A file whose size and time have not changed
    for 60 s is filed as the test's next take, or moved to inbox/Refused with a .txt saying why. A file with no test
    id stays, listed under Untagged on the Lab tab. lab.inbox_extra folders are COPIED in, never moved."""
    try:
        root = capture_root(acct)
    except LabError as e:
        return [f"  {acct.name}: {e}"]
    inbox = root / "inbox"
    if not inbox.is_dir():
        return []
    seen_p = lab_state(acct) / "inbox-seen.json"
    seen = read_json(seen_p)
    sigs, copied = dict(seen.get("sigs") or {}), dict(seen.get("copied") or {})
    lines: List[str] = []
    wall = time.time()

    def settled(p: Path) -> bool:
        try:
            st = p.stat()
        except OSError:
            return False
        sig = [st.st_size, st.st_mtime_ns]
        prev = sigs.get(str(p))
        if not prev or prev.get("sig") != sig or st.st_size == 0:
            sigs[str(p)] = {"sig": sig, "first": wall}
            return False
        return wall - float(prev.get("first") or wall) >= settle_s

    for extra in settings(acct).get("inbox_extra") or []:          # copied, never moved
        d = C.expand(str(extra))
        for p in sorted(d.iterdir()) if d.is_dir() else []:
            if not (p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")):
                continue
            st = p.stat()
            key = f"{p}|{st.st_size}|{st.st_mtime_ns}"
            if key in copied or not settled(p):
                continue
            dest = inbox / p.name
            if dest.exists():
                dest = inbox / f"{p.stem} (from {d.name}){p.suffix}"
            shutil.copy2(p, dest)
            copied[key] = str(dest)
            lines.append(f"  {acct.name}: copied {p.name} from {d} into the inbox (the original stays)")
    for p in sorted(inbox.iterdir()):
        if not (p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")):
            continue
        m = TAKE_NAME.match(p.name)
        if not m or not settled(p):
            continue
        tid = m.group("id").upper()
        try:
            sc_path, msg = file_take(cfg, acct, tid, p, "inbox", now=now, strict=True)
        except LabError as e:
            dest = _refuse_into(p, inbox / "Refused", str(e))
            lines.append(f"  {acct.name} {p.name}: REFUSED into inbox/Refused/{dest.name}: {e}")
            continue
        sigs.pop(str(p), None)
        start_checks(sc_path, background=background)      # the page answers at once; the inbox job waits
        lines.append(f"  {acct.name} {p.name}: {msg}")
    live = {k: v for k, v in sigs.items() if Path(k).exists()}
    write_json(seen_p, {"sigs": live, "copied": copied})
    return lines


def untagged(acct: C.Account) -> List[dict]:
    """Recordings no test owns yet: every video in <capture root>/_obs (a take started in OBS itself, or after a
    crash; never the one being recorded now) and every video in the inbox whose name carries no test id."""
    try:
        root = capture_root(acct)
    except LabError:
        return []
    rec = recording(acct)
    since = float(rec.get("started_ts") or 0) if rec else None
    out = []
    for where in ("_obs", "inbox"):
        d = root / where
        for p in sorted(d.iterdir()) if d.is_dir() else []:
            if not (p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")):
                continue
            if where == "inbox" and TAKE_NAME.match(p.name):
                continue
            st = p.stat()
            if where == "_obs" and since is not None and st.st_mtime >= since - 5:
                continue
            out.append({"file": str(p), "name": p.name, "where": where, "bytes": st.st_size,
                        "at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return out


def tag(cfg: C.Config, acct: C.Account, file: str, tid: str, by: str = "", now: Optional[datetime] = None) -> str:
    """An untagged recording into a test, as its next take (the same refusals as the inbox; the file stays put when
    refused)."""
    root = capture_root(acct)
    p = Path(os.path.realpath(str(file or "")))
    allowed = [Path(os.path.realpath(root / "_obs")), Path(os.path.realpath(root / "inbox"))]
    if not any(p.parent == a for a in allowed) or not p.is_file():
        raise LabError("That file is not an untagged recording (Capture/_obs or Capture/inbox)")
    rec = recording(acct)
    if rec and p.parent == allowed[0] and p.stat().st_mtime >= float(rec.get("started_ts") or 0) - 5:
        raise LabError("That file is the take being recorded now")
    sc_path, msg = file_take(cfg, acct, tid, p, "untagged", now=now, strict=True, by=by)
    start_checks(sc_path)
    return msg


# ------------------------------------------------------------------------------------------------------ the taps
PAGE_ONLY = ("lab-outcome", "lab-tag", "lab-allow", "lab-drop", "lab-relook-answer")
OUTCOMES = ("as_expected", "different", "redo")


def _person(cfg: C.Config, acct: C.Account, by: object) -> str:
    from . import signoff as S
    by = str(by or "").strip()
    if not by or S.BOT_VERIFIED.search(by):
        raise LabError("refused: an outcome is confirmed by a person on the approvers list, never by a bot")
    try:
        return S._who(cfg, by, acct)
    except SystemExit as e:
        raise LabError(str(e)) from None


def _in_black(sc: dict) -> List[int]:
    spans = (sc.get("checks") or {}).get("black_spans") or []
    return [int(m["n"]) for m in sc.get("marks") or []
            if any(float(a) - 0.2 <= float(m.get("t") or 0) <= float(b) + 0.2 for a, b in spans)]


def outcome(cfg: C.Config, acct: C.Account, tid: str, kind: str, text: str, seen: str, by: str,
            now: Optional[datetime] = None, relook: bool = False) -> str:
    """Andrew's outcome on the current take: As expected, Different (what he saw), or Redo. Only from the page
    (act checks it): his name, at least one mark, the seen key of what the page showed, the checks done, no mark
    inside a black span."""
    now = now or cfg.now()
    who = _person(cfg, acct, by)
    if kind not in OUTCOMES:
        raise LabError(f"the outcome is one of {', '.join(OUTCOMES)}")
    text = " ".join(str(text or "").split())[:600]
    if kind == "different" and not text:
        raise LabError("Different needs what happened, in a few words")
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        allowed = ("relook",) if relook else ("recorded", "verified", "relook")
        if t["status"] not in allowed:
            raise LabError(f"{t['id']} is {t['status']}: " + ("nothing to look again at" if relook else
                                                              "its outcome is fixed now"))
        tk = current_take(t)
        if not tk:
            raise LabError(f"{t['id']} has no take")
        sc_path = Path(tk["sidecar"])
        with sidecar_edit(sc_path) as sc:
            if seen_key(sc, sc_path) != str(seen or ""):
                raise LabError("NOT done: the take or its marks changed since the page showed them. Look at it again")
            if kind != "redo":
                if not sc.get("marks"):
                    raise LabError("Mark the moment first: an outcome needs at least one mark")
                if checks_pending(sc):
                    start_checks(sc_path)
                    raise LabError("The take is still being checked (its sha256, black spans and sound): try again "
                                   "in a minute")
                black = _in_black(sc)
                if black:
                    raise LabError(f"Mark {', '.join(map(str, black))} is in a black span (the card lost the picture): "
                                   f"Redo, or mark a moment with a picture")
            o = {"kind": kind, "text": text, "confirmed_by": who, "confirmed_at": iso(now), "seen": seen_key(sc, sc_path),
                 "via": "page", "doubt_shown": (t.get("expected") or {}).get("check") in CHECK_OPEN}
            if sc.get("outcome"):
                sc.setdefault("outcome_before", []).append(sc["outcome"])
            sc["outcome"] = o
            note(sc, f"outcome {kind} by {who}" + (f": {text}" if text else ""), now)
        t["outcome"] = {"kind": kind, "text": text, "confirmed_by": who, "confirmed_at": o["confirmed_at"],
                        "take": tk.get("n")}
        t.pop("relook", None)
        if kind == "redo":
            set_status(t, "redo", f"{who}: Redo" + (f" ({text})" if text else ""), now)
        else:
            set_status(t, "verified", f"{who}: {'as expected' if kind == 'as_expected' else 'different: ' + text}", now)
    log(acct, "outcome", test=t["id"], take=tk.get("n"), kind=kind, by=who, relook=relook)
    if kind == "redo":
        return f"{t['id']}: Redo. It is back on the list to record."
    return f"{t['id']}: {'As expected' if kind == 'as_expected' else 'Different'}, confirmed by {who}. The next Lab run scripts it."


def allow(cfg: C.Config, acct: C.Account, tid: str, by: str, now: Optional[datetime] = None) -> str:
    who = _person(cfg, acct, by)
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        if t["status"] != "held":
            raise LabError(f"{t['id']} is not held")
        h = t.get("hold") or {}
        t.setdefault("holds_lifted", []).append(dict(h, lifted_by=who, lifted_at=iso(now or cfg.now())))
        t["hold"] = None
        set_status(t, _unheld_status(t), f"hold lifted by {who} ({h.get('why')})", now)
    log(acct, "allow", test=t["id"], by=who)
    return f"{t['id']} allowed by {who}: it can be dealt and recorded now."


def drop(cfg: C.Config, acct: C.Account, tid: str, why: str, by: str, now: Optional[datetime] = None) -> str:
    who = _person(cfg, acct, by)
    why = " ".join(str(why or "").split())
    if not why:
        raise LabError("Drop needs a why")
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        if t["status"] in ("placed", "posted"):
            raise LabError(f"{t['id']} is {t['status']}: Decline its clip on the Clips tab instead")
        t["hold"] = None
        t["dropped"] = {"why": why[:300], "by": who, "at": iso(now or cfg.now())}
        set_status(t, "dropped", f"dropped by {who}: {why[:300]}", now)
    log(acct, "drop", test=t["id"], by=who, why=why)
    return f"{t['id']} dropped: it is never dealt again."


def skip(cfg: C.Config, acct: C.Account, tid: str, now: Optional[datetime] = None) -> str:
    now = now or cfg.now()
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        t["skip_until"] = now.date().isoformat()
        note(t, "skipped today on the Lab tab", now)
    return f"{t['id']} skipped for today."


def _lab_acct(cfg: C.Config, slug: object) -> C.Account:
    accts = lab_accounts(cfg)
    if slug:
        acct = cfg.account(str(slug))
        if not is_lab(acct):
            raise LabError(f"{acct.name} is not a Lab account")
        if acct.machine != cfg.machine:
            raise LabError(f"{acct.name} runs on the machine {acct.machine!r}")
        return acct
    if len(accts) != 1:
        raise LabError("say which Lab account")
    return accts[0]


def act(cfg: C.Config, body: dict, page: bool = False, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """One tap on the Lab tab: lab-record | lab-mark | lab-unmark | lab-stop | lab-outcome | lab-tag | lab-skip |
    lab-drop | lab-allow | lab-relook-answer | lab-open-obs. The outcome, the tag, the hold, the drop and the relook
    answer are taken only when `page` (review._from_page: a browser request from the review page itself); no command
    and no bot can set an outcome. (did it, what to say)"""
    op = str(body.get("op") or "")
    if op in PAGE_ONLY and not page:
        return False, ("Refused: an outcome, a tag, a hold or a drop is set only by Andrew, on the Lab tab of Clip "
                       "Review. No command or script does it.")
    try:
        acct = _lab_acct(cfg, body.get("account"))
        tid = str(body.get("test") or "").upper()
        by = str(body.get("by") or "")
        if op == "lab-record":
            return True, record(cfg, acct, tid, by=by, cheats_ok=body.get("cheats_ok") is True, now=now)
        if op == "lab-stop":
            return True, stop(cfg, acct, by=by, now=now)
        if op == "lab-mark":
            at = body.get("t")
            return True, mark(cfg, acct, tid, at=float(at) if isinstance(at, (int, float)) else None,
                              label=str(body.get("label") or ""), now=now)
        if op == "lab-unmark":
            return True, unmark(cfg, acct, tid, int(body.get("n") or 0), now=now)
        if op == "lab-outcome":
            return True, outcome(cfg, acct, tid, str(body.get("kind") or ""), str(body.get("text") or ""),
                                 str(body.get("seen") or ""), by, now=now)
        if op == "lab-relook-answer":
            return True, outcome(cfg, acct, tid, "different", str(body.get("text") or ""), str(body.get("seen") or ""),
                                 by, now=now, relook=True)
        if op == "lab-tag":
            _person(cfg, acct, by)
            return True, tag(cfg, acct, str(body.get("file") or ""), tid, by=by, now=now)
        if op == "lab-allow":
            return True, allow(cfg, acct, tid, by, now=now)
        if op == "lab-drop":
            return True, drop(cfg, acct, tid, str(body.get("why") or ""), by, now=now)
        if op == "lab-skip":
            return True, skip(cfg, acct, tid, now=now)
        if op == "lab-open-obs":
            from . import obs as O
            return True, O.open_app()
    except LabError as e:
        return False, str(e)
    raise SystemExit(f"unknown action {op!r}")


# ------------------------------------------------------------------------------------------------------ evidence
def frame_dir(sc_path: Path) -> Path:
    return Path(sc_path).parent / "frames" / Path(sc_path).stem


def _grab(src: Path, at: float, out: Path, vf: str, timeout: int = 120) -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error", "-ss", f"{max(0.0, at):.3f}", "-i",
                            str(src), "-frames:v", "1", "-vf", vf, "-q:v", "3", str(out)],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and out.is_file()


def hud_script(acct: C.Account) -> Path:
    return acct.skill_dir / "scripts" / "hud.py"


def hud_stars(acct: C.Account, video: Path, ats: List[float], window: float = 2.0) -> Dict[float, int]:
    """The skill's star reader (hud.py stars): the most wanted stars lit within +-window s of each time."""
    from . import makers as M
    script = hud_script(acct)
    if not script.is_file():
        raise LabError(f"the skill has no star reader yet ({script}: hud.py stars)")
    cmd = [M.PY_SKILLS, str(script), "stars", str(video), "--at", ",".join(f"{a:.2f}" for a in ats),
           "--window", f"{window:g}", "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(script.parent),
                           env=M.gta_env(acct))
    except (OSError, subprocess.SubprocessError) as e:
        raise LabError(f"hud.py stars did not run ({type(e).__name__})") from None
    if r.returncode != 0:
        raise LabError("hud.py stars refused: " + ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1][:200])
    try:
        data = json.loads((r.stdout or "").strip().splitlines()[-1])
        return {round(float(k), 2): int(v) for k, v in data.items() if v is not None}
    except (ValueError, IndexError, AttributeError):
        raise LabError("hud.py stars said nothing readable") from None


def _test_take(acct: C.Account, tid: str) -> Tuple[dict, dict, Path, dict]:
    bank = bank_read(acct)
    t = find(bank, tid)
    tk = current_take(t)
    if not tk:
        raise LabError(f"{t['id']} has no take yet")
    sc_path = Path(tk["sidecar"])
    sc = read_json(sc_path)
    if not sc:
        raise LabError(f"{t['id']}: its sidecar {sc_path} is gone")
    return t, tk, sc_path, sc


def frames(cfg: C.Config, acct: C.Account, tid: str, focus: float = 0.5) -> List[str]:
    """The evidence frames of the current take (factory.py lab frames): at each mark -3, -1.5, 0, +1, +2.5 and +5 s
    as 960-wide JPEGs of the 16:9 frame, a 9:16 crop at `focus`, a contact strip of one frame a second (6x5, one image
    per 30 s), the stars at each mark when the take's HUD is on, and frames.json with the take's sha256."""
    t, tk, sc_path, sc = _test_take(acct, tid)
    if not sc.get("marks"):
        raise LabError(f"{t['id']}: no marks on its take yet")
    if checks_pending(sc):
        finish_checks(sc_path)
        sc = read_json(sc_path)
    src = take_file(sc, sc_path)
    if not src.is_file():
        raise LabError(f"{t['id']}: the take {src} is gone")
    dur = float((sc.get("probe") or {}).get("duration_s") or 0)
    if not 0.0 <= focus <= 1.0:
        raise LabError("--focus is 0 to 1 (0.5 = the middle of the frame)")
    out_dir = frame_dir(sc_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    marks_out = []
    for m in sc["marks"]:
        n, at = int(m["n"]), float(m["t"])
        names = []
        for off in OFFSETS:
            name = f"m{n}{off:+.1f}.jpg"
            want = min(max(0.0, at + off), max(0.0, dur - 0.3))
            # the very end of a take may hold no whole frame: step back a little rather than lose the frame
            if any(_grab(src, max(0.0, want - back), out_dir / name, "scale=960:-2") for back in (0.0, 0.5, 1.0)):
                names.append(name)
        crop = f"crop-m{n}.jpg"
        vf = f"crop=ih*9/16:ih:(iw-ih*9/16)*{focus:.3f}:0,scale=540:-2"
        marks_out.append({"n": n, "t": at, "label": m.get("label") or "", "frames": names,
                          "crop": crop if _grab(src, at, out_dir / crop, vf) else None})
    for old in out_dir.glob("strip-*.jpg"):
        old.unlink()
    try:
        subprocess.run(["ffmpeg", "-y", "-nostats", "-loglevel", "error", "-hwaccel", "auto", "-i", str(src), "-vf",
                        "fps=1,scale=320:-2,tile=6x5", "-q:v", "4", str(out_dir / "strip-%03d.jpg")],
                       capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError):
        pass
    strip = sorted(p.name for p in out_dir.glob("strip-*.jpg"))
    lines = []
    if sc.get("hud") == "on":
        try:
            reads = hud_stars(acct, src, [m["t"] for m in marks_out])
            for m in marks_out:
                if round(m["t"], 2) in reads:
                    m["hud"] = {"stars": reads[round(m["t"], 2)]}
        except LabError as e:
            lines.append(f"  stars not read: {e}")
    doc = {"test": t["id"], "take": tk.get("n"), "sha256": sc.get("sha256"), "offsets": list(OFFSETS), "focus": focus,
           "marks": marks_out, "strip": strip, "strip_every_s": 1.0, "made_at": iso(cfg.now())}
    write_json(out_dir / "frames.json", doc)
    rel = out_dir.relative_to(sc_path.parent)
    lines.insert(0, f"{t['id']} take {tk.get('n')}: {sum(len(m['frames']) for m in marks_out)} frames at "
                    f"{len(marks_out)} mark(s), {len(strip)} strip image(s) in {out_dir}")
    for m in marks_out:
        lines.append(f"  mark {m['n']} at {_clock(m['t'])}" + (f" ({m['label']})" if m["label"] else "") + ": "
                     + ", ".join(f"{rel}/{x}" for x in m["frames"])
                     + (f"; stars {m['hud']['stars']}" if m.get("hud") else ""))
    log(acct, "frames", test=t["id"], take=tk.get("n"))
    return lines


# ------------------------------------------------------------------------------------------------------ variety
def spoken(text: str) -> str:
    """A say line as heard: holds (<0.8>) out, [written|spoken] pairs as written."""
    s = re.sub(r"<\s*\d+(?:\.\d+)?\s*>", " ", str(text or ""))
    s = re.sub(r"\[([^\]|]*)\|[^\]]*\]", r"\1", s)
    return " ".join(s.split())


def opener(script: dict) -> str:
    beats = script.get("beats") or []
    words = re.findall(r"[a-z0-9']+", spoken((beats[0] if beats else {}).get("say", "")).lower())
    return " ".join(words[:4])


def scripts_index(acct: C.Account, bank: Optional[dict] = None) -> List[dict]:
    """Every accepted script of the account: its test, when it was written, structure, area, kind, opener, verdict,
    strap and question, and where it was placed."""
    bank = bank if bank is not None else bank_read(acct)
    out = []
    for t in bank.get("tests") or []:
        p = t.get("script")
        if not p:
            continue
        s = read_json(Path(p))
        if not s:
            continue
        v = s.get("verdict") or {}
        out.append({"test": t["id"], "status": t.get("status"), "written_at": str(s.get("written_at") or ""),
                    "structure": str(s.get("structure") or ""), "area": t.get("area"), "kind": t.get("kind"),
                    "game": t.get("game"), "opener": opener(s), "verdict": str(v.get("line") or ""),
                    "strap": str(v.get("strap") or ""), "question": str(s.get("question") or ""),
                    "placed": t.get("placed"), "viral": t.get("viral") or 0})
    return sorted(out, key=lambda x: x["written_at"])


def variety(cfg: C.Config, acct: C.Account, day: Optional[date] = None, now: Optional[datetime] = None) -> dict:
    """What the scripts and the boards have used (factory.py lab variety): structures, areas and openers in the last
    opener_days, the last verdict_window verdicts, and the structures and areas on day D's board."""
    now = now or cfg.now()
    vs = settings(acct)["variety"]
    idx = scripts_index(acct)
    since = (now - timedelta(days=int(vs["opener_days"]))).isoformat()
    recent = [x for x in idx if x["written_at"] >= since]
    out = {"structures": list(vs["structures"]),
           "used": {"structures": _count(x["structure"] for x in recent), "areas": _count(x["area"] for x in recent),
                    "openers": sorted({x["opener"] for x in recent if x["opener"]})},
           "verdicts": [x["verdict"] for x in idx[-int(vs["verdict_window"]):] if x["verdict"]],
           "last_strap": idx[-1]["strap"] if idx else "", "rules": dict(vs)}
    if day:
        bd = B.read(acct, day) or {}
        on_day = [{"slot": k, "test": (s.get("lab") or {}).get("test"), "structure": (s.get("lab") or {}).get("structure"),
                   "status": s.get("status")} for k, s in sorted((bd.get("slots") or {}).items()) if s.get("lab")]
        out["day"] = {"date": day.isoformat(), "slots": acct.slots_for(day), "lab": on_day}
    return out


def _count(xs) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for x in xs:
        if x:
            out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# ------------------------------------------------------------------------------------------------------ the script
NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
             "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
             "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "twice": 2, "once": 1}
SCALES = {"hundred": 100, "thousand": 1000, "million": 1000000}
NOT_A_NUMBER_BEFORE_ONE = {"no", "any", "every", "some", "the", "this", "that", "which", "each", "anyone", "someone"}


def numbers_in(text: str) -> set:
    """Every number a line says, as digits or as words ("four stars", "4 STARS", "twenty-five", "250,000"); the game
    label (GTA 5, GTA VI) is not one, and "one" as a pronoun ("no one", "the one") is not either."""
    s = str(text or "").lower()
    for pat in GAME_LABEL.values():
        s = pat.sub(" ", s)
    s = re.sub(r"\b1-999-[\d-]+\b", " ", s)
    toks = re.findall(r"\d[\d,]*(?:\.\d+)?|[a-z]+", s)
    out, i = set(), 0
    while i < len(toks):
        w = toks[i]
        if w[0].isdigit():
            try:
                v = float(w.replace(",", ""))
                out.add(int(v) if v == int(v) else v)
            except ValueError:
                pass
            i += 1
            continue
        if w in NUM_WORDS:
            if w == "one" and ((i > 0 and toks[i - 1] in NOT_A_NUMBER_BEFORE_ONE)
                               or (i + 1 < len(toks) and toks[i + 1] in ("of", "another", "more"))):
                i += 1
                continue
            v = NUM_WORDS[w]
            if 20 <= v <= 90 and v % 10 == 0 and i + 1 < len(toks) and toks[i + 1] in NUM_WORDS \
                    and 1 <= NUM_WORDS[toks[i + 1]] <= 9 and toks[i + 1] not in ("once", "twice"):
                v += NUM_WORDS[toks[i + 1]]
                i += 1
            while i + 1 < len(toks) and toks[i + 1] in SCALES:
                v *= SCALES[toks[i + 1]]
                i += 1
            out.add(v)
        i += 1
    return out


STARS_SAID = re.compile(r"\b(\d+|zero|one|two|three|four|five|six)[\s-]+(?:wanted[\s-]+)?stars?\b", re.I)


def stars_said(text: str) -> List[int]:
    return [int(m.group(1)) if m.group(1).isdigit() else NUM_WORDS[m.group(1).lower()]
            for m in STARS_SAID.finditer(str(text or ""))]


def labreel_script(acct: C.Account) -> Path:
    return acct.skill_dir / "scripts" / "labreel.py"


def _skill_check(acct: C.Account, script_path: Path, sc_path: Path) -> Tuple[List[str], Optional[float]]:
    """The skill's own check of a script (labreel.py check-script: the voice-length estimate at the kit's voice,
    the game labels, the say_as coverage, every beat inside the take)."""
    from . import makers as M
    reel = labreel_script(acct)
    if not reel.is_file():
        return [f"the skill's TEST lane is not in yet ({reel}: check-script): the script cannot be checked"], None
    try:
        r = subprocess.run([M.PY_SKILLS, str(reel), "check-script", str(script_path), "--sidecar", str(sc_path)],
                           capture_output=True, text=True, timeout=180, cwd=str(reel.parent), env=M.gta_env(acct))
    except (OSError, subprocess.SubprocessError) as e:
        return [f"labreel.py check-script did not run ({type(e).__name__})"], None
    try:
        data = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [f"labreel.py check-script said nothing readable (exit {r.returncode}): "
                + ((r.stderr or "").strip().splitlines() or [""])[-1][:200]], None
    probs = [f"the skill: {p}" for p in data.get("problems") or []]
    if r.returncode not in (0, 2) and not probs:
        probs.append(f"labreel.py check-script exit {r.returncode}")
    est = data.get("seconds_est")
    return probs, float(est) if isinstance(est, (int, float)) else None


def check_script(cfg: C.Config, acct: C.Account, tid: str, path: Path,
                 now: Optional[datetime] = None) -> Tuple[List[str], dict]:
    """Every check a Lab script must pass before it is accepted (the truth rule, the safety words, the game labels,
    the variety rules), then the skill's own check-script. (problems, what was read)."""
    now = now or cfg.now()
    path = Path(path)
    probs: List[str] = []
    try:
        s = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return [f"{path} is not readable JSON ({e})"], {}
    if not isinstance(s, dict):
        return [f"{path} is not an object"], {}
    bank = bank_read(acct)
    t = find(bank, tid)
    if t["status"] not in ("verified", "scripted"):
        return [f"{t['id']} is {t['status']}: only a verified test is scripted"], {}
    tk = current_take(t)
    if not tk:
        return [f"{t['id']} has no take"], {}
    sc_path = Path(tk["sidecar"])
    sc = read_json(sc_path)
    o = sc.get("outcome") or {}
    vs = settings(acct)["variety"]
    game = t.get("game")
    # 1. a person confirmed it on the page, on what the page showed
    if o.get("via") != "page" or o.get("kind") not in ("as_expected", "different"):
        probs.append("the take has no outcome confirmed on the Lab tab")
    else:
        try:
            _person(cfg, acct, o.get("confirmed_by"))
        except LabError:
            probs.append(f"the outcome's confirmed_by {o.get('confirmed_by')!r} is not on the approvers list")
        if o.get("seen") != seen_key(sc, sc_path):
            probs.append("the take or its marks changed after Andrew confirmed it: he confirms it again first")
    if s.get("test") != t["id"]:
        probs.append(f"script.test is {s.get('test')!r}, not {t['id']}")
    if s.get("game") != game:
        probs.append(f"script.game is {s.get('game')!r}; the test is {game}")
    so = s.get("outcome") or {}
    if (so.get("kind"), so.get("confirmed_by"), so.get("confirmed_at")) != (o.get("kind"), o.get("confirmed_by"),
                                                                            o.get("confirmed_at")):
        probs.append("script.outcome is not the take's confirmed outcome (kind, confirmed_by, confirmed_at)")
    # 2. the evidence is this take's
    rec = s.get("recording") or {}
    f = take_file(sc, sc_path)
    try:
        st = f.stat()
        if st.st_size != sc.get("bytes") or (sc.get("mtime_ns") and st.st_mtime_ns != sc.get("mtime_ns")):
            probs.append(f"the take {f.name} changed on disk since it was filed")
    except OSError:
        probs.append(f"the take {f} is gone")
    if not sc.get("sha256"):
        probs.append("the take's sha256 is not measured yet (factory.py lab frames measures it)")
    elif rec.get("sha256") != sc.get("sha256"):
        probs.append("script.recording.sha256 is not the take's")
    if int(rec.get("take") or 0) != int(tk.get("n") or 0):
        probs.append(f"script.recording.take is {rec.get('take')}; the current take is {tk.get('n')}")
    fdir = frame_dir(sc_path)
    fj = read_json(fdir / "frames.json")
    listed = set()
    if not fj:
        probs.append(f"no frames.json for this take: run factory.py lab frames {acct.slug} {t['id']} first")
    elif fj.get("sha256") != sc.get("sha256"):
        probs.append("frames.json is from another take (its sha256): run factory.py lab frames again")
    else:
        rel = fdir.relative_to(sc_path.parent)
        for m in fj.get("marks") or []:
            listed |= {f"{rel}/{x}" for x in (m.get("frames") or [])}
            if m.get("crop"):
                listed.add(f"{rel}/{m['crop']}")
        listed |= {f"{rel}/{x}" for x in fj.get("strip") or []}
    marks = {int(m["n"]): float(m["t"]) for m in sc.get("marks") or []}
    observed = {}
    for ob in s.get("observed") or []:
        n = int(ob.get("mark") or 0)
        if n not in marks:
            probs.append(f"observed names mark {n}, which the take does not have")
            continue
        if not str(ob.get("shows") or "").strip():
            probs.append(f"observed mark {n} says nothing it shows")
        for fr in ob.get("frames") or []:
            if fr not in listed:
                probs.append(f"observed mark {n}: {fr} is not one of this take's frames (frames.json)")
            elif not (sc_path.parent / fr).is_file():
                probs.append(f"observed mark {n}: {fr} is gone")
        if not ob.get("frames"):
            probs.append(f"observed mark {n} names no frame it was read from")
        observed[n] = ob
    # 3. every claim has a mark, every mark is on screen
    beats = s.get("beats") or []
    spans = {}
    dur = float((sc.get("probe") or {}).get("duration_s") or 0)
    total = 0.0
    for b in beats:
        try:
            a, z = float(b["src"][0]), float(b["src"][1])
        except (KeyError, TypeError, ValueError, IndexError):
            probs.append(f"beat {b.get('id')!r} has no src [start, end]")
            continue
        if not 0 <= a < z <= dur + 0.05:
            probs.append(f"beat {b.get('id')!r} src {a}-{z} is outside the take (0-{dur:.1f})")
        if z - a < 0.5:
            probs.append(f"beat {b.get('id')!r} is under half a second")
        spans[str(b.get("id"))] = (a, z)
        total += max(0.0, z - a)
        foc = b.get("focus", 0.5)
        if not (foc == "track" or (isinstance(foc, (int, float)) and 0 <= float(foc) <= 1)):
            probs.append(f"beat {b.get('id')!r} focus is a number 0-1 or \"track\"")
    if not beats:
        probs.append("the script has no beats")
    if not acct.min_seconds - 0.05 <= total <= acct.max_seconds + 0.05:
        probs.append(f"the beats run {total:.1f}s of the take; {acct.name} clips are {acct.min_seconds:.0f} to "
                     f"{acct.max_seconds:.0f} seconds")
    v = s.get("verdict") or {}
    claims = v.get("claims") or []
    if not claims:
        probs.append("the verdict has no claims")
    vbeat = str(v.get("beat") or "")
    if vbeat not in spans:
        probs.append(f"verdict.beat {vbeat!r} is not one of the beats")
    claim_marks = []
    for c in claims:
        ms = [int(x) for x in c.get("marks") or [] if str(x).lstrip("-").isdigit()]
        if not ms:
            probs.append(f"the claim {c.get('claim')!r} names no mark")
        for n in ms:
            if n not in marks:
                probs.append(f"the claim {c.get('claim')!r} names mark {n}, which the take does not have")
                continue
            claim_marks.append(n)
            if n not in observed:
                probs.append(f"mark {n} backs a claim but observed has nothing for it")
            if not any(a <= marks[n] <= z for a, z in spans.values()):
                probs.append(f"mark {n} ({_clock(marks[n])}) backs a claim but no beat shows it: the proof must be in "
                             f"the clip")
    if vbeat in spans and claim_marks and not any(spans[vbeat][0] <= marks[n] <= spans[vbeat][1] for n in claim_marks):
        probs.append(f"the verdict beat {vbeat!r} holds none of its claims' marks")
    # 4. the basis follows the outcome; 7. a doubtful test's verdict is the recording's
    check = (t.get("expected") or {}).get("check")
    basis = v.get("basis")
    if o.get("kind") == "different":
        if basis != "outcome":
            probs.append("Andrew said Different: verdict.basis is \"outcome\" (his words), never the bank's answer")
    elif o.get("kind") == "as_expected":
        if check in CHECK_OPEN:
            if basis != "observed":
                probs.append(f"{t['id']}'s check is {check} (the recording decides): verdict.basis is \"observed\", "
                             f"never \"expected\"")
            if not o.get("doubt_shown"):
                probs.append("As expected on a doubtful test counts only when the card showed the doubt line")
        elif basis != "expected":
            probs.append("Andrew said As expected: verdict.basis is \"expected\"")
    # 5. numbers
    exp = t.get("expected") or {}
    allowed = set()
    for text in [ob.get("shows") for ob in observed.values()] + [o.get("text")]:
        allowed |= numbers_in(text)
    if o.get("kind") == "as_expected" and check not in CHECK_OPEN:
        allowed |= numbers_in(exp.get("answer")) | numbers_in(exp.get("check_note"))
    said_by = {"verdict.card": v.get("card"), "verdict.line": v.get("line"),
               "platform_caption": s.get("platform_caption"),
               "the verdict beat": next((b.get("say") for b in beats if str(b.get("id")) == vbeat), "")}
    for where, text in said_by.items():
        extra = numbers_in(spoken(text)) - allowed
        if extra:
            probs.append(f"{where} says {', '.join(str(x) for x in sorted(extra, key=str))}, which neither the "
                         f"outcome nor the frames say")
    # 6. stars, read by machine
    named = stars_said(v.get("card")) + stars_said(v.get("line"))
    if named and sc.get("hud") == "on":
        ats = sorted({marks[n] for n in claim_marks if n in marks})
        try:
            reads = hud_stars(acct, f, ats)
            got = max(reads.values()) if reads else None
            if got is None or any(x != got for x in named):
                probs.append(f"the verdict says {', '.join(map(str, sorted(set(named))))} stars; hud.py reads "
                             f"{got if got is not None else 'none'} at the claim marks "
                             f"({', '.join(f'{k}: {val}' for k, val in sorted(reads.items()))})")
        except LabError as e:
            probs.append(f"the verdict names stars and they cannot be read: {e}")
    # safety words, game labels, title, hook, question, structure
    hook = s.get("hook") or {}
    texts = {"title": s.get("title"), "hook.card": hook.get("card"), "hook.spoken": hook.get("spoken"),
             "verdict.card": v.get("card"), "verdict.line": v.get("line"), "platform_caption": s.get("platform_caption"),
             "question": s.get("question")}
    texts.update({f"beat {b.get('id')}": spoken(b.get("say")) for b in beats})
    for where, text in texts.items():
        hit = BANNED.search(str(text or ""))
        if hit:
            probs.append(f"{where} says {hit.group(0)!r} (no glitches, exploits, mods or trainers on Lab)")
        if game == "GTA 5":
            g6 = GTA6_WORDS.search(str(text or ""))
            if g6:
                probs.append(f"{where} says {g6.group(0)!r}: a GTA 5 test never names GTA 6")
        if where in ("title", "platform_caption", "verdict.line", "question") and DASHES.search(str(text or "")):
            probs.append(f"{where} has a dash")
    title = str(s.get("title") or "").strip()
    if not title:
        probs.append("no title")
    else:
        if len(title) > 70:
            probs.append(f"the title is {len(title)} characters (70 at most)")
        if not (title.endswith("?") or title.lower().startswith("the secret")):
            probs.append("the title is a question, or \"The secret ... in GTA 5\"")
        if not GAME_LABEL[game].search(title):
            probs.append(f"the title does not name {game}")
        extra = set(content_words(title)) - set(content_words(t["question"])) - {"happen", "secret"}
        if extra:
            probs.append(f"the title says {', '.join(sorted(extra))}, which the test's question does not")
    if not str(hook.get("card") or "").strip():
        probs.append("no hook card (the question, on screen from the first frame)")
    if not GAME_LABEL[game].search(str(hook.get("spoken") or "")):
        probs.append(f"the spoken hook does not name {game}")
    q = str(s.get("question") or "").strip()
    if not q:
        probs.append("no comment question")
    elif q.lower() in STOCK_QUESTIONS:
        probs.append("the comment question is the skill's stock line: write one for this test")
    elif q.rstrip("?").lower() == title.rstrip("?").lower():
        probs.append("the comment question is the title again")
    if s.get("structure") not in vs["structures"]:
        probs.append(f"structure {s.get('structure')!r} is not one of {', '.join(vs['structures'])}")
    if not str(v.get("strap") or "").strip():
        probs.append("no verdict strap")
    if not (s.get("safety") or {}).get("checked"):
        probs.append("safety.checked is empty: say what was checked on the frames (cutscenes, mission text, radio)")
    # 18. variety
    idx = [x for x in scripts_index(acct, bank) if x["test"] != t["id"]]
    since = (now - timedelta(days=int(vs["opener_days"]))).isoformat()
    op = opener(s)
    reuse = [x["test"] for x in idx if x["written_at"] >= since and x["opener"] and x["opener"] == op]
    if op and reuse:
        probs.append(f"the opener \"{op}\" was used by {reuse[0]} in the last {vs['opener_days']} days")
    line = str(v.get("line") or "")
    for x in idx[-int(vs["verdict_window"]):]:
        if x["verdict"] and difflib.SequenceMatcher(None, x["verdict"].lower(), line.lower()).ratio() >= float(
                vs["verdict_similar"]):
            probs.append(f"the verdict line is too like {x['test']}'s (\"{x['verdict'][:60]}\")")
            break
    if idx and idx[-1]["strap"] and idx[-1]["strap"].strip().upper() == str(v.get("strap") or "").strip().upper():
        probs.append(f"the strap {v.get('strap')!r} is the last script's too: rotate it")
    skill, est = _skill_check(acct, path, sc_path)
    probs += skill
    return probs, {"test": t["id"], "take": tk.get("n"), "seconds_src": round(total, 1), "seconds_est": est,
                   "structure": s.get("structure"), "sidecar": str(sc_path)}


def accept_script(cfg: C.Config, acct: C.Account, tid: str, path: Path, check: bool = False,
                  now: Optional[datetime] = None) -> Tuple[bool, List[str]]:
    """factory.py lab script: every check of check_script; with none failing (and no --check), the script is kept as
    <date>/T001.script.json beside its take and the test is scripted."""
    probs, info = check_script(cfg, acct, tid, path, now=now)
    if probs:
        return False, [f"REFUSED: {tid} script, {len(probs)} problem(s), nothing written:"] + [f"  - {p}" for p in probs]
    head = (f"{info['test']} take {info['take']}: the script passes ({info['structure']}, {info['seconds_src']}s of "
            f"the take" + (f", voice about {info['seconds_est']:.0f}s" if info.get("seconds_est") else "") + ")")
    if check:
        return True, [head, "  --check: nothing written"]
    sc_path = Path(info["sidecar"])
    dest = sc_path.with_name(f"{sc_path.stem}.script.json")
    path = Path(path)
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        if t["status"] not in ("verified", "scripted"):
            return False, [f"REFUSED: {t['id']} is {t['status']} now"]
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
        t["script"] = str(dest)
        t["render_refusals"] = 0
        set_status(t, "scripted", f"script accepted ({info['structure']})", now)
    log(acct, "script", test=tid, take=info["take"], structure=info["structure"])
    return True, [head, f"  {tid} scripted: {dest}"]


def redo(cfg: C.Config, acct: C.Account, tid: str, kind: str, why: str, now: Optional[datetime] = None) -> List[str]:
    """factory.py lab redo (the Claude task): relook = the frames do not show what Andrew confirmed, or show nothing
    (back to him: Look again); redo = the take cannot be used (black, HDR, radio, a cutscene, the wrong place)."""
    why = " ".join(str(why or "").split())
    if kind not in ("relook", "redo"):
        raise LabError("--kind is relook or redo")
    if not why:
        raise LabError("--why says what the frames show")
    with bank_edit(acct) as bank:
        t = find(bank, tid)
        if t["status"] not in ("recorded", "verified", "scripted", "relook", "rendered"):
            raise LabError(f"{t['id']} is {t['status']}: nothing to send back")
        if kind == "relook":
            tk = current_take(t) or {}
            fj = read_json(frame_dir(Path(tk.get("sidecar") or "/x")) / "frames.json") if tk else {}
            t["relook"] = {"why": why[:600], "at": iso(now or cfg.now()), "take": tk.get("n"),
                           "frames": [f"{m.get('n')}:{x}" for m in fj.get("marks") or [] for x in m.get("frames") or []][:12]}
        set_status(t, kind, why[:600], now)
    log(acct, "redo", test=tid, kind=kind, why=why)
    return [f"{tid}: {'back to Andrew to look again' if kind == 'relook' else 'a new take is needed'} ({why[:200]})"]


# ------------------------------------------------------------------------------------------------------ the maker
DELIVERED = re.compile(r"^DELIVERED\s+(\S+)\s+(\S+)\s+([\d.]+)\s*$", re.M)
REFUSED = re.compile(r"^REFUSED\s+([A-Z][A-Z0-9_-]*):\s*(.*)$", re.M)
FILLED = (B.READY, B.APPROVED, B.SCHEDULED, B.MAKING)


def _day_lab(board: dict) -> Dict[str, dict]:
    """slot -> its lab block, for the day's filled (or being made) Lab slots."""
    return {k: s["lab"] for k, s in (board.get("slots") or {}).items()
            if s.get("lab") and s.get("status") in FILLED}


def choose(acct: C.Account, day: date, slot: str, board: dict, bank: dict, planned: Dict[str, dict],
           exclude: set) -> Tuple[Optional[dict], str]:
    """The test for one slot: a scripted (or rendered, not placed) test of the day's games (post_games, first
    first), then the most viral, within the day's variety (neighbouring slots differ in structure; one structure or
    one area at most structure_share / area_share of the day; at most max_per_kind of one kind). A repeat only when
    the stock forces it, and said. (the test, a note: '' or why the rules gave way)"""
    st = settings(acct)
    vs, mk = st["variety"], int(st["deal"]["max_per_kind"])
    games = post_games(acct, day)
    idx = {x["test"]: x for x in scripts_index(acct, bank)}
    cands = [t for t in bank.get("tests") or [] if t.get("status") in ("scripted", "rendered") and not t.get("placed")
             and t.get("game") in games and t["id"] not in exclude and t["id"] in idx
             and not opens(acct, t, day) and t["id"] not in {p["test"] for p in planned.values()}]
    if not cands:
        return None, ""
    cands.sort(key=lambda t: (games.index(t["game"]), t.get("status") != "rendered", -int(t.get("viral") or 0), t["id"]))
    day_lab = dict(_day_lab(board))
    day_lab.update(planned)
    n = max(1, len(board.get("slots") or {}))
    s_cap = max(1, int(float(vs["structure_share"]) * n))
    a_cap = max(1, int(float(vs["area_share"]) * n))
    order = sorted(board.get("slots") or {})
    i = order.index(slot) if slot in order else -1
    near = [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order) and i >= 0]

    def fits(t: dict) -> str:
        x = idx[t["id"]]
        others = [v for k, v in day_lab.items() if k != slot]
        if sum(1 for v in others if v.get("structure") == x["structure"]) >= s_cap:
            return f"a third {x['structure']}" if s_cap == 2 else f"one {x['structure']} too many"
        if sum(1 for v in others if v.get("area") == t.get("area")) >= a_cap:
            return f"one {t.get('area')} test too many"
        if sum(1 for v in others if v.get("kind") == t.get("kind")) >= mk:
            return f"more than {mk} {t.get('kind')} tests"
        if vs.get("neighbours_differ") and any((day_lab.get(k) or {}).get("structure") == x["structure"] for k in near):
            return f"{x['structure']} next to {x['structure']}"
        return ""
    for t in cands:
        if not fits(t):
            return t, ""
    t = cands[0]
    return t, f"a repeat the stock forces ({fits(t)}): no scripted test fits the day's variety rules"


def _render(cfg: C.Config, acct: C.Account, day: date, slot: str, t: dict, log_p: Path) -> Tuple[object, str, str, str]:
    """labreel.py build for one test, in the render queue. (exit code | 'timeout', job, slug, the words)"""
    from . import makers as M
    from . import renderlock as RL
    st = settings(acct)
    reel = labreel_script(acct)
    tk = current_take(t) or {}
    job = f"lab-{t['id']}-{day.strftime('%Y%m%d')}"
    cmd = [M.PY_SKILLS, str(reel), "build", "--script", str(t.get("script")), "--sidecar", str(tk.get("sidecar")),
           "--job", job, "--date", day.isoformat(), "--slot", slot.replace(":", ""), "--deliver"]
    limit = float(st["render_timeout_minutes"]) * 60
    _, stop_at = RL.night_limits()
    if stop_at is not None:
        limit = max(60.0, min(limit, stop_at - time.time()))
    try:
        at = log_p.stat().st_size if log_p.exists() else 0
    except OSError:
        at = 0
    try:
        with RL.hold(acct.slug, f"lab {t['id']}: render", log=log_p):
            r = M.run_logged(cmd, log_p, cwd=reel.parent, env=M.gta_env(acct), timeout=int(limit))
        rc: object = r.returncode
    except RL.Busy as e:
        return 75, job, "", str(e)
    except subprocess.TimeoutExpired:
        return "timeout", job, "", f"ran past {int(limit // 60)} minutes and was stopped"
    try:
        with open(log_p) as f:
            f.seek(at)
            out = f.read()
    except OSError:
        out = ""
    d = DELIVERED.findall(out)
    if rc == 0:
        if not d:
            return 3, job, "", "labreel.py exited 0 with no DELIVERED line"
        return 0, d[-1][0], d[-1][1], f"{d[-1][2]}s"
    if rc == 2:
        refs = REFUSED.findall(out)
        return 2, job, "", "; ".join(f"{g}: {w}" for g, w in refs) or M.tail(log_p, 2)
    return rc, job, "", M.tail(log_p, 2)


def _notice(text: str) -> None:
    from . import net
    if net.why_not() in ("offline", "a test run"):
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title "
                        f"{json.dumps('Lab')}"], capture_output=True, timeout=30)


def make(cfg: C.Config, acct: C.Account, day: date, only: Optional[List[str]] = None, dry_run: bool = False,
         now: Optional[datetime] = None) -> List[str]:
    """The maker "lab": each open or failed slot of the day (a same-day slot only when same_day_lead_hours away)
    gets a scripted test, rendered by the skill's TEST lane in the render queue and placed as an unsigned draft
    (place.place: the skill's words, every refusal of a placed clip). Exit 2 (a gate refused) tries the next test
    and counts against this one (two refusals: back to Andrew); exit 3 (the tool itself) and 75 (past the deadline)
    end the run."""
    from . import makers as M
    lines = sync_status(cfg, acct)
    reel = labreel_script(acct)
    if not reel.is_file() and not dry_run:
        return lines + [f"{acct.name}: the skill has no TEST lane yet ({reel}): nothing rendered"]
    lock = M.render_lock(acct)
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lf:
        if not dry_run:
            fcntl.flock(lf, fcntl.LOCK_EX)
        start = now or cfg.now()
        lead = timedelta(hours=float(settings(acct)["same_day_lead_hours"]))
        with B.edit(cfg, acct, day) as bd:
            board = json.loads(json.dumps(bd))
        todo = []
        for k, s in sorted(board["slots"].items()):
            if s.get("status") not in (B.OPEN, B.FAILED) or (only and k not in only):
                continue
            if cfg.slot_time(day, k) - start < lead:
                lines.append(f"  {k}: under {lead.total_seconds() / 3600:g} h away: left open")
                continue
            todo.append(k)
        if not todo:
            return lines + [f"{acct.name} {day}: no open slot to make"]
        planned: Dict[str, dict] = {}
        log_p = M.log_path(acct, day, "lab")
        fixed = now
        for k in todo:
            now = fixed or cfg.now()             # a render takes up to 45 minutes: the clock is read for each slot
            if cfg.slot_time(day, k) - now < lead:
                lines.append(f"  {k}: under {lead.total_seconds() / 3600:g} h away now: left open")
                continue
            tried: set = set()
            while True:
                bank = bank_read(acct)
                t, why = choose(acct, day, k, board, bank, planned, tried)
                if t is None:
                    lines.append(f"  {k}: no scripted test left for {', '.join(post_games(acct, day))} (left open)")
                    break
                tried.add(t["id"])
                idx = {x["test"]: x for x in scripts_index(acct, bank)}
                planned[k] = {"test": t["id"], "structure": idx[t["id"]]["structure"], "area": t.get("area"),
                              "kind": t.get("kind")}
                if dry_run:
                    lines.append(f"  {k}: would make {t['id']} ({idx[t['id']]['structure']}, {t.get('area')})"
                                 + (f"; {why}" if why else ""))
                    break
                got = _make_one(cfg, acct, day, k, t, why, log_p, now, fresh=fixed is None)
                lines.append(got[1])
                if got[0] == "next":
                    planned.pop(k, None)
                    continue
                if got[0] == "stop":
                    return lines
                if got[0] != "ok":
                    planned.pop(k, None)
                break
        if not dry_run:
            lines += sync_status(cfg, acct)
    return lines


def _make_one(cfg: C.Config, acct: C.Account, day: date, k: str, t: dict, why: str, log_p: Path,
              now: datetime, fresh: bool = True) -> Tuple[str, str]:
    """One slot from one test. ('ok' | 'next' (try another test) | 'stop' (end the run) | 'failed', the line).
    fresh: place reads the clock again after the render (its 90-minute lead is from the moment it places)."""
    from . import makers as M
    from . import place as PL
    tid = t["id"]
    if t.get("status") == "rendered" and t.get("job") and t.get("slug"):
        rc, job, slug, words = 0, t["job"], t["slug"], "rendered before"
    else:
        with B.edit(cfg, acct, day) as bd:
            s = bd["slots"][k]
            if s.get("status") not in (B.OPEN, B.FAILED):
                return "failed", f"  {k}: filled meanwhile ({s.get('status')})"
            s["status"] = B.MAKING
            B.note(s, f"making Lab {tid}" + (f" ({why})" if why else ""))
        rc, job, slug, words = _render(cfg, acct, day, k, t, log_p)
    if rc != 0:
        kind = {2: "refused", 3: "tool", 75: "deadline", "timeout": "timeout"}.get(rc, "run")
        M._fail(cfg, acct, day, [k], f"Lab {tid}: {kind}: {words}"[:600], kind=kind)
        if rc == 2:
            with bank_edit(acct) as bank:
                tt = find(bank, tid)
                tt["render_refusals"] = int(tt.get("render_refusals") or 0) + 1
                if tt["render_refusals"] >= 2:
                    tt["relook"] = {"why": f"the render refused it twice: {words}"[:600], "at": iso(now)}
                    set_status(tt, "relook", f"refused twice at the render: {words}"[:500], now)
                else:
                    note(tt, f"render refused ({words})"[:500], now)
            log(acct, "render-refused", test=tid, slot=k, why=words)
            return "next", f"  {k}: {tid} REFUSED at the render ({words[:200]}); trying the next test"
        log(acct, "render-failed", test=tid, slot=k, kind=kind, why=words)
        if rc in (3, 75):
            return "stop", f"  {k}: FAILED ({kind}: {words[:200]}); nothing more is made in this run"
        return "failed", f"  {k}: FAILED ({kind}: {words[:200]})"
    with bank_edit(acct) as bank:
        tt = find(bank, tid)
        tt["job"], tt["slug"] = job, slug
        if tt.get("status") != "rendered":
            set_status(tt, "rendered", f"rendered as {job} ({words})", now)
    with B.edit(cfg, acct, day) as bd:
        s = bd["slots"][k]
        if s.get("status") == B.MAKING:
            s["status"] = B.OPEN
    ok, said = PL.place(cfg, acct, day, k, job=job, slug=slug, fmt="test", why=f"Lab {tid}: {t.get('question')}",
                        now=None if fresh else now)
    if not ok:
        M._fail(cfg, acct, day, [k], f"Lab {tid} rendered but not placed: " + " ".join(x.strip() for x in said)[:500],
                kind="place")
        return "failed", f"  {k}: {tid} rendered, NOT placed: " + " ".join(x.strip() for x in said)[:300]
    tk = current_take(t) or {}
    sc_path = Path(tk.get("sidecar") or "/nonexistent")
    sc = read_json(sc_path)
    script = read_json(Path(t.get("script") or "/nonexistent"))
    fdir = frame_dir(sc_path)
    claims = [int(n) for c in (script.get("verdict") or {}).get("claims") or [] for n in c.get("marks") or []
              if str(n).isdigit()]
    marks = {int(m["n"]): float(m["t"]) for m in sc.get("marks") or []}
    evidence = []
    for n in dict.fromkeys(claims):
        fr = fdir / f"m{n}+0.0.jpg"
        evidence.append({"mark": n, "t": marks.get(n), "frame": str(fr) if fr.is_file() else ""})
    o = sc.get("outcome") or {}
    lab_block = {"test": tid, "take": tk.get("n"), "game": t.get("game"), "structure": script.get("structure"),
                 "area": t.get("area"), "kind": t.get("kind"), "job": job,
                 "outcome": {k2: o.get(k2) for k2 in ("kind", "text", "confirmed_by", "confirmed_at")},
                 "expected": (t.get("expected") or {}).get("answer") or "",
                 "verdict": (script.get("verdict") or {}).get("line") or "", "evidence": evidence,
                 "recording": str(take_file(sc, sc_path)), "script": str(t.get("script") or "")}
    with B.edit(cfg, acct, day) as bd:
        bd["slots"][k]["lab"] = lab_block
    with bank_edit(acct) as bank:
        tt = find(bank, tid)
        tt["placed"] = {"day": day.isoformat(), "slot": k, "at": iso(now)}
        set_status(tt, "placed", f"placed in {day} {k} as a draft", now)
    log(acct, "place", test=tid, day=day.isoformat(), slot=k, job=job)
    if day == now.date():
        _notice(f"Lab: {tid} is ready to watch for {k}")
    return "ok", f"  {k}: {tid} ready to approve ({job}, {words})" + (f"; {why}" if why else "")


def sync_status(cfg: C.Config, acct: C.Account) -> List[str]:
    """The boards back into the bank: a placed test whose slot went out on a platform is posted; one whose clip was
    declined (or sent back to be made again) goes back to verified with the reason, so the next scripting run writes
    it again; a second decline holds it for Andrew."""
    try:
        bank = bank_read(acct)
    except LabError as e:
        return [f"  {acct.name}: {e}"]
    placed = {(t["placed"]["day"], t["placed"]["slot"]): t["id"] for t in bank.get("tests") or []
              if t.get("placed") and t.get("status") in ("placed", "posted")}
    if not placed:
        return []
    changes: List[Tuple[str, str, str]] = []
    for (d, k), tid in placed.items():
        try:
            bd = B.read(acct, date.fromisoformat(d)) or {}
        except ValueError:
            continue
        s = (bd.get("slots") or {}).get(k)
        if not s or (s.get("lab") or {}).get("test") != tid:
            if s is not None and not s.get("lab") and s.get("status") in (B.OPEN, B.FAILED):
                changes.append((tid, "declined", "the slot was made again without it"))
            continue
        out = [p for p, r in (s.get("outlets") or {}).items() if r.get("post_id") or r.get("video_id")]
        if out and s.get("status") in B.SIGNED:
            changes.append((tid, "posted", ", ".join(out)))
        elif s.get("status") == B.DECLINED or (s.get("status") in (B.OPEN, B.FAILED) and not s.get("clip")):
            why = next((n.split(" ", 1)[-1] for n in reversed(s.get("notes") or []) if "declined by" in n
                        or "sent back to be made again" in n), "declined")
            changes.append((tid, "declined", why))
    lines = []
    if not changes:
        return []
    with bank_edit(acct) as bank:
        for tid, what, why in changes:
            t = find(bank, tid)
            if what == "posted":
                if t["status"] != "posted":
                    set_status(t, "posted", f"posted on {why}")
                    lines.append(f"  {tid}: posted ({why})")
                continue
            if t["status"] != "placed":
                continue
            t.setdefault("declined", []).append(dict(t.get("placed") or {}, why=why[:300], at=B.utcnow()))
            t["placed"], t["job"], t["slug"] = None, None, None
            t.setdefault("scripts_before", []).append(t.get("script"))
            t["script"] = None
            if len(t["declined"]) >= 2:
                t["hold"] = {"why": f"declined twice ({why[:200]})", "since": date.today().isoformat(), "was": "verified"}
                set_status(t, "held", f"declined a second time: {why[:300]}")
                lines.append(f"  {tid}: declined a second time: held for Andrew")
            else:
                set_status(t, "verified", f"declined ({why[:300]}): the next scripting run writes it again")
                lines.append(f"  {tid}: declined, back to verified for a new script")
    return lines


# ------------------------------------------------------------------------------------------------------ the deal
def next_posting_day(cfg: C.Config, now: Optional[datetime] = None) -> date:
    """The day today's takes are for: tomorrow (recording runs 06:15 to 20:45; the 20:05 run makes tomorrow)."""
    return ((now or cfg.now()) + timedelta(days=1)).date()


def stock(acct: C.Account, bank: dict, day: date) -> List[dict]:
    games = post_games(acct, day)
    return [t for t in bank.get("tests") or [] if t.get("status") in STOCK and not t.get("placed")
            and t.get("game") in games]


def deal(cfg: C.Config, acct: C.Account, day: Optional[date] = None, now: Optional[datetime] = None) -> dict:
    """What the Lab tab asks Andrew to record: the posting day's slot count under the dated rule, less what that day
    already has (filled slots, and the stock on its way), then deal.extra more "if you have time". Batched by area
    (the area with the most viral score first), then the viral score; at most max_per_kind of one kind; never a held,
    dropped or skipped test, or one before its game's date or not_before."""
    now = now or cfg.now()
    day = day or next_posting_day(cfg, now)
    st = settings(acct)
    bank = bank_read(acct)
    slots = acct.slots_for(day)
    filled = len(_day_lab(B.read(acct, day) or {}))
    have = stock(acct, bank, day)
    need = max(0, len(slots) - filled - len(have))
    games = post_games(acct, day)
    today = now.date().isoformat()
    cands = [t for t in bank.get("tests") or [] if t.get("status") in ("todo", "redo") and t.get("game") in games
             and not opens(acct, t, now.date()) and str(t.get("skip_until") or "") < today]
    score: Dict[str, int] = {}
    for t in cands:
        score[t.get("area") or ""] = score.get(t.get("area") or "", 0) + int(t.get("viral") or 0)
    cands.sort(key=lambda t: (-score.get(t.get("area") or "", 0), t.get("area") or "", t.get("status") != "redo",
                              -int(t.get("viral") or 0), t["id"]))
    mk = int(st["deal"]["max_per_kind"])
    picks: List[dict] = []
    kinds: Dict[str, int] = {}
    want = need + int(st["deal"]["extra"])
    for t in cands:
        if len(picks) >= want:
            break
        if kinds.get(t.get("kind") or "", 0) >= mk:
            continue
        kinds[t.get("kind") or ""] = kinds.get(t.get("kind") or "", 0) + 1
        picks.append(t)
    return {"day": day.isoformat(), "slots": slots, "rule": (acct.volume_rule(day) or {}).get("why", ""),
            "filled": filled, "stock": len(have), "need": need, "games": games,
            "record": [t["id"] for t in picks[:need]], "extra": [t["id"] for t in picks[need:]]}


def waiting(acct: C.Account, bank: dict, now: Optional[datetime] = None) -> Dict[str, List[dict]]:
    """What waits for Andrew: takes with no mark or no outcome (stale after unconfirmed_stale_days), and relooks."""
    now = now or datetime.now(timezone.utc)
    stale = timedelta(days=float(settings(acct)["unconfirmed_stale_days"]))
    out: Dict[str, List[dict]] = {"outcome": [], "relook": [], "stale": []}
    for t in bank.get("tests") or []:
        if t.get("status") == "relook":
            out["relook"].append(t)
        elif t.get("status") == "recorded":
            out["outcome"].append(t)
            tk = current_take(t) or {}
            try:
                at = datetime.fromisoformat(str(tk.get("at")))
                at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
                if now - at > stale:
                    out["stale"].append(t)
            except ValueError:
                pass
    return out


def task_stamp(acct: C.Account) -> dict:
    return read_json(lab_state(acct) / "task-last.json")


def status(cfg: C.Config, acct: C.Account, day: Optional[date] = None, stamp: bool = False,
           now: Optional[datetime] = None) -> dict:
    """factory.py lab status: what waits for a script (with the paths the task needs), the stock, the rule in force.
    `stamp` (the --json the scripting task runs): its last run is written for doctor and the morning line."""
    now = now or cfg.now()
    bank = bank_read(acct)
    day = day or next_posting_day(cfg, now)
    counts: Dict[str, int] = {}
    for t in bank.get("tests") or []:
        counts[t.get("status")] = counts.get(t.get("status"), 0) + 1
    todo = []
    for t in bank.get("tests") or []:
        if t.get("status") != "verified":
            continue
        tk = current_take(t) or {}
        sc = read_json(Path(tk.get("sidecar") or "/nonexistent"))
        todo.append({"id": t["id"], "game": t.get("game"), "question": t.get("question"), "kind": t.get("kind"),
                     "area": t.get("area"), "hud": t.get("hud"), "take": tk.get("n"), "file": str(take_file(sc, Path(
                         tk.get("sidecar") or "/x"))) if sc else "", "sidecar": tk.get("sidecar"),
                     "script_to": str(Path(tk.get("sidecar") or "/x").with_name(
                         f"{Path(tk.get('sidecar') or '/x').stem}.script.json")),
                     "marks": sc.get("marks") or [], "outcome": sc.get("outcome"),
                     "expected": t.get("expected"), "safety": t.get("safety"), "declined": t.get("declined") or [],
                     "sha256": sc.get("sha256"), "checks_pending": checks_pending(sc) if sc else True})
    boards = {}
    for d in (now.date(), day):
        bd = B.read(acct, d) or {}
        boards[d.isoformat()] = {"slots": acct.slots_for(d), "board": {k: s.get("status") for k, s in
                                                                      sorted((bd.get("slots") or {}).items())},
                                 "games": post_games(acct, d), "rule": (acct.volume_rule(d) or {}).get("why", ""),
                                 "report": str(bd.get("report") or "")}
    out = {"account": acct.slug, "active": acct.active, "now": iso(now), "posting_day": day.isoformat(),
           "game_now": game_on(acct, now.date()), "counts": counts, "to_script": todo,
           "relook": [t["id"] for t in bank.get("tests") or [] if t.get("status") == "relook"],
           "scripted": [t["id"] for t in bank.get("tests") or [] if t.get("status") == "scripted"],
           "stock_for_posting_day": len(stock(acct, bank, day)), "boards": boards,
           "skill_lane": labreel_script(acct).is_file(), "task_last": task_stamp(acct)}
    if stamp:
        write_json(lab_state(acct) / "task-last.json", {"at": iso(now), "to_script": len(todo)})
    return out


def status_lines(cfg: C.Config, acct: C.Account, day: Optional[date] = None) -> List[str]:
    st = status(cfg, acct, day)
    c = st["counts"]
    out = [f"{acct.name} ({'on' if acct.active else 'switched off: takes are kept and scripted, nothing is rendered or posted'})"
           f", recording {game_on(acct, cfg.now().date())} now"]
    out.append("  the bank: " + ", ".join(f"{v} {k}" for k, v in sorted(c.items())))
    for d, b in st["boards"].items():
        out.append(f"  {d}: {len(b['slots'])} slots ({', '.join(b['slots'])})" + (f", {b['rule']}" if b["rule"] else "")
                   + f"; games {', '.join(b['games'])}")
    out.append(f"  stock for {st['posting_day']}: {st['stock_for_posting_day']}; to script: "
               f"{', '.join(x['id'] for x in st['to_script']) or 'none'}; relook: {', '.join(st['relook']) or 'none'}")
    if not st["skill_lane"]:
        out.append(f"  the skill's TEST lane is not in yet ({labreel_script(acct)}): nothing can be checked or rendered")
    return out


# ------------------------------------------------------------------------------------------------------ the Clips card
def card_lines(cfg: C.Config, acct: C.Account, s: dict) -> str:
    """A Lab slot's box on its Clips card: what Andrew saw next to what the clip says, the evidence frames, the take
    at the mark. Lab clips never sign themselves."""
    import html
    import urllib.parse
    lab = s.get("lab") or {}
    if not lab:
        return ""

    def e(x: object) -> str:
        return html.escape(str(x), quote=True)

    def url(p: str, frag: str = "") -> str:
        return f"/file?p={urllib.parse.quote(p)}" + frag
    o = lab.get("outcome") or {}
    saw = {"as_expected": "As expected", "different": "Different"}.get(str(o.get("kind")), str(o.get("kind") or "?"))
    if o.get("kind") == "different" and o.get("text"):
        saw += f": {o['text']}"
    when = str(o.get("confirmed_at") or "")[11:16]
    ev = "".join(f'<img src="{e(url(x["frame"]))}" alt="mark {e(x.get("mark"))}" loading="lazy">'
                 for x in lab.get("evidence") or [] if x.get("frame"))
    first = next((x for x in lab.get("evidence") or [] if x.get("t") is not None), None)
    link = (f'<a href="{e(url(lab["recording"], "#t=" + format(float(first["t"]), ".1f")))}" target="_blank" '
            f'rel="noopener">the take at mark {e(first["mark"])}</a>' if first and lab.get("recording") else "")
    return (f'<div class="lab-box"><b>Lab {e(lab.get("test"))} · {e(lab.get("structure") or "")} · '
            f'{e(lab.get("game") or "")}</b>'
            f'<div><span>You saw</span> {e(saw)} ({e(o.get("confirmed_by") or "?")}{", " + e(when) if when else ""})</div>'
            f'<div><span>The clip says</span> {e(lab.get("verdict") or "")}</div>'
            + (f'<div class="lab-ev">{ev}</div>' if ev else "") + (f"<div>{link}</div>" if link else "")
            + '<div class="note">Lab clips wait for you: they never sign themselves.</div></div>')


CARD_CSS = """
.lab-box{border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:8px 0;font-size:13px}
.lab-box span{color:var(--sub);margin-right:4px}.lab-box .note{color:var(--sub);margin-top:4px}
.lab-ev{display:flex;gap:4px;margin-top:6px}.lab-ev img{width:31%;aspect-ratio:16/9;object-fit:cover;border-radius:3px}
"""


# ------------------------------------------------------------------------------------------------------ doctor
def kit_paths(acct: C.Account) -> Tuple[Path, Path]:
    kit = acct.kit or "lab"
    return acct.skill_dir / "assets" / "brand" / kit / "kit.json", acct.skill_dir / "presets" / f"{kit}.json"


def voice_stamp(acct: C.Account) -> Tuple[bool, str]:
    """The kit's own voice passed voice_audit.py: assets/brand/<kit>/voice-audit.json holds "passed": true and the
    sha256 of the kit's voice block (json, keys sorted) as "spec_sha256" (or the block itself as "voice")."""
    kit_p, _ = kit_paths(acct)
    stamp_p = kit_p.parent / "voice-audit.json"
    kit = read_json(kit_p)
    if not kit:
        return False, "voice audit: no kit yet"
    voice = kit.get("voice") or {}
    if not stamp_p.is_file():
        return False, f"voice audit: no {stamp_p.name} beside the kit (voice_audit.py --kit {acct.kit} --lines lab)"
    st = read_json(stamp_p)
    spec = hashlib.sha256(json.dumps(voice, sort_keys=True).encode()).hexdigest()
    same = st.get("spec_sha256") == spec or (isinstance(st.get("voice"), dict) and st.get("voice") == voice)
    if not st.get("passed"):
        return False, f"voice audit: {stamp_p.name} says it did not pass"
    if not same:
        return False, "voice audit: the kit's voice changed since it passed (run voice_audit.py again)"
    if "am_michael" in json.dumps(voice):
        return False, "voice audit: the kit's voice uses Gaming's am_michael"
    return True, f"voice audit passed for the kit's voice ({str(st.get('at') or '')[:10]})"


def doctor_rows(cfg: C.Config, acct: C.Account, reach: bool = True) -> List[Tuple[bool, str, str, bool]]:
    """(done, text, fix, must) for a Lab account: a checklist while it is off, NO lines once it is on (must). Never
    a password; OBS is only asked who it is (reach)."""
    rows: List[Tuple[bool, str, str, bool]] = []
    st = settings(acct)
    kit_p, preset_p = kit_paths(acct)
    gone = [str(x.relative_to(acct.skill_dir)) for x in (kit_p, preset_p) if not x.is_file()]
    rows.append((not gone, f'brand kit "{acct.kit or "(none)"}" in {acct.skill_dir.name}'
                 + (f": {', '.join(gone)} missing" if gone else ""),
                 "the skill's work: assets/brand/lab/kit.json and presets/lab.json (it extends gta)", True))
    rows.append((labreel_script(acct).is_file(), "the skill's TEST lane (scripts/labreel.py)"
                 + ("" if labreel_script(acct).is_file() else ": not in yet, so nothing is checked or rendered"),
                 "merge the skill's lab/test-lane branch", True))
    ok_v, words = voice_stamp(acct)
    rows.append((ok_v, words, f"render three voices, run voice_audit.py --kit {acct.kit} --lines lab, Andrew picks one",
                 True))
    kit = read_json(kit_p)
    kday = str(((kit.get("posting") or {}).get("game_by_date") or {}).get("from") or "")
    mine = str(st["game_by_date"].get("from") or "")
    rule_days = {str(r.get("from")) for r in (acct.raw or {}).get("volume") or [] if r.get("from")}
    pg_days = {str(r.get("from")) for r in st.get("post_games") or [] if r.get("from")}
    same = (not kday or kday == mine) and rule_days <= {mine} and pg_days <= {mine}
    rows.append((same, f"one launch date: lab.game_by_date {mine}, volume {', '.join(sorted(rule_days)) or '-'}, "
                       f"post_games {', '.join(sorted(pg_days)) or '-'}, the kit {kday or '(no kit date)'}",
                 "set the same day in lab.game_by_date.from, the volume rule's from/until, lab.post_games and the "
                 "kit's posting.game_by_date.from", False))
    op = obs_path(acct)
    if op.is_file():
        mode = op.stat().st_mode & 0o777
        dmode = op.parent.stat().st_mode & 0o777
        rows.append((mode == 0o600 and dmode == 0o700, f"OBS settings {op} (mode {mode:o}, folder {dmode:o})",
                     f"chmod 600 '{op}'; chmod 700 '{op.parent}'", True))
    else:
        rows.append((False, f"OBS settings {op}: not written yet", f"factory.py lab obs-setup {acct.slug} (in the Mac's "
                     f"own Terminal: the password at a hidden prompt)", False))
    if reach and op.is_file():
        h = obs_check(acct)
        rows.append((bool(h.get("ok")), f"OBS: {h.get('why')}" + (f" (OBS {h.get('obs')}, websocket {h.get('ws')}; "
                     f"{h.get('picture_words')})" if h.get("ok") else ""),
                     "open OBS (factory.py lab obs-check); Tools > WebSocket Server Settings: on, with a password",
                     False))
    try:
        root = capture_root(acct)
        rows.append((True, f"capture root {root} (outside iCloud)", "", True))
        link = acct.account_dir / str(st["capture_link"])
        good = link.is_symlink() and Path(os.path.realpath(link)) == Path(os.path.realpath(root))
        rows.append((good, f"{acct.account_dir.name}/{link.name} links to the capture root"
                     + ("" if good else (": missing" if not link.exists() and not link.is_symlink() else
                                         ": not a link to it")), f"factory.py lab setup {acct.slug}", False))
        if root.is_dir():
            gb = free_gb(root)
            rows.append((gb >= float(st["free_gb"]["warn"]), f"{gb:.0f} GB free on the capture volume (Record refuses "
                         f"under {st['free_gb']['refuse']}, warns under {st['free_gb']['warn']})",
                         f"factory.py lab tidy {acct.slug} --dry-run", False))
        else:
            rows.append((False, f"capture root {root}: not made yet", f"factory.py lab setup {acct.slug}", False))
    except LabError as e:
        rows.append((False, str(e), "lab.capture_root under ~/Movies", True))
    try:
        bank = bank_read(acct)
        probs = bank_problems(bank)
        n = len(bank.get("tests") or [])
        rows.append((n > 0 and not probs, f"the bank {bank_path(acct).name}: {n} tests"
                     + (f"; {probs[0]}" if probs else ""),
                     f"factory.py lab import-bank {acct.slug} --from <research> --check <check>" if not n else
                     "fix it with factory.py lab bank", True))
    except LabError as e:
        rows.append((False, f"the bank: {e}", "keep one copy of tests.json", True))
    shared = [(o.name, sorted(set(o.slots) & set(acct.slots))) for o in cfg.accounts.values()
              if o.slug != acct.slug and set(o.slots) & set(acct.slots)]
    if shared:                               # Lab took the parked Roleplay's minutes: said if both are on
        rows.append((False, f"{acct.name} posts in the same minutes as {shared[0][0]} ({', '.join(shared[0][1][:3])}"
                     + (" ..." if len(shared[0][1]) > 3 else "") + ")", "Andrew's call: move one account's slots "
                     "(config/accounts.json slots, and the volume rule's)", False))
    today = cfg.now().date()
    for d in (today, today + timedelta(days=1)):
        r = acct.volume_rule(d)
        rows.append((True, f"{d}: {len(acct.slots_for(d))} slots" + (f" ({r.get('why')})" if r and r.get("why") else ""),
                     "", False))
    from . import autosign as A
    rows.append((not A.on(cfg, acct), "auto_sign off (every Lab clip waits for Andrew's Approve)",
                 f'"auto_sign": {{"on": false}} on {acct.slug}', True))
    rows.append(((acct.raw or {}).get("drop_folder") is False, '"drop_folder": false (no Clip Drop folder for it)',
                 f'"drop_folder": false on {acct.slug}', True))
    ts = task_stamp(acct)
    rows.append((bool(ts), "the scripting task's last run: " + (str(ts.get("at")) if ts else "never"),
                 "the Claude task lab-scripts-auto (14:05 and 20:05, saved in Auto) runs factory.py lab status "
                 f"{acct.slug} --json first", False))
    return rows


def morning_line(cfg: C.Config, now: Optional[datetime] = None) -> str:
    """The 06:30 notice's Lab line: takes waiting for Andrew's outcome, relooks, and verified takes the scripting
    task has not scripted ('' when nothing waits)."""
    now = now or cfg.now()
    parts = []
    for acct in lab_accounts(cfg):
        try:
            bank = bank_read(acct)
        except LabError as e:
            parts.append(f"{acct.short or acct.name}: {e}")
            continue
        w = waiting(acct, bank, now)
        if w["outcome"]:
            n = len(w["outcome"])
            parts.append(f"{n} take{'s wait' if n != 1 else ' waits'} for your outcome")
        if w["relook"]:
            parts.append(f"{len(w['relook'])} to look again")
        verified = [t for t in bank.get("tests") or [] if t.get("status") == "verified"]
        ts = task_stamp(acct)
        try:
            last = datetime.fromisoformat(str(ts.get("at"))) if ts else None
        except ValueError:
            last = None
        if verified and (last is None or now - last > timedelta(hours=20)):
            parts.append(f"not scripted yet: {len(verified)}")
    return ("Lab: " + "; ".join(parts) + ".") if parts else ""


def tidy(acct: C.Account) -> List[str]:
    """factory.py lab tidy --dry-run: each take's size and whether it is spent (its test posted or dropped, or a later
    take replaced it). It lists only: the factory never deletes a take."""
    bank = bank_read(acct)
    out, spent_b = [], 0
    for t in bank.get("tests") or []:
        for tk in t.get("takes") or []:
            p = Path(tk.get("file") or "")
            try:
                size = p.stat().st_size
            except OSError:
                continue
            spent = t.get("status") in ("posted", "dropped") or tk.get("n") != t.get("take")
            spent_b += size if spent else 0
            out.append(f"  {'spent' if spent else 'kept '} {size / 1e9:6.1f} GB  {t['id']} take {tk.get('n')} "
                       f"({t.get('status')}): {p}")
    return out + [f"  {spent_b / 1e9:.1f} GB in spent takes. Nothing was deleted: delete by hand what you no longer "
                  f"want (and its sidecar stays as the record)."]
