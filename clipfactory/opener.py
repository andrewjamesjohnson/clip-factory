"""THE OPENER RULE (Daily 8 step 3; the plan Andrew took 10.28, "Ask these in order. The first yes wins."; his
"build them all", 10.29). Every Politics clip opens on ONE thing, picked in this order:

    1. two people clashing?   the VS card (vscard.py), then the punch line
    2. opens on a shout?      nothing: the shout is the hook (the engine's OUTBURST cold open, or the clip's own)
    3. a calm start?          a pre-hook (prehook.py), then the punch line: no fight and no strong line in the
                              clip's own first 3 seconds (after the engine's cold open, if it made one)
    4. everything else        the punch line only (the engine's cold open), or the clip's own strong first line

Never a VS card and a pre-hook together. The rule decides WHETHER a clip takes a pre-hook; the pre-hook test
(learn.py e-0001) then splits the calm clips A/B while it runs, and the fallback (prehook.fallback) gives every
other calm clip its pre-hook. What a clip opened on is written to slot["opener"] at collect, for Clip Review and
the coach.

    strong line   a cue in the clip's own first 3 s that asks (?) or shouts (!), a room label ([BOOING]), or a
                  word of heat (STRONG_WORDS): the same reading the coach gives a first line
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from . import board as B

CALM_FOR = 3.0                          # the clip's own first seconds that must hold a strong line
SHOUT_FROM = "THE OUTBURST's shout"      # the engine's cold open record for an OUTBURST's shout (clipkit._cold_shout)
FIGHT_LOOKS = ("faceoff",)               # a two-person look is a fight even when its VS card could not be made
STRONG_WORDS = (
    "liar", "lie", "lied", "lying", "lies", "fraud", "corrupt", "crook", "criminal", "disgrace", "disgraceful",
    "disgusting", "shameful", "shame", "how dare", "shut up", "resign", "traitor", "treason", "garbage", "idiot",
    "stupid", "insane", "crazy", "outrageous", "ridiculous", "nonsense", "pathetic", "coward", "cover-up",
    "cover up", "perjury", "under oath", "answer the question", "yes or no", "excuse me", "stop", "wrong",
    "absolutely not", "never",
)
_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def cues(srt: Path) -> List[Tuple[float, float, str]]:
    """(start, end, text) of each cue in an .srt, in seconds; [] when it cannot be read."""
    try:
        raw = Path(srt).read_text(errors="replace")
    except OSError:
        return []
    out = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        m = _TS.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = " ".join(block[m.end():].split())
        if text:
            out.append((a, b, text))
    return out


def strong(text: str) -> str:
    """Why a cue's words are a strong line ('' when they are not)."""
    t = str(text).strip()
    if not t:
        return ""
    label = re.search(r"\[[^\]]+\]", t)
    if label:
        return f"a room label {label.group(0)}"     # (no backslash inside an f-string: the factory runs on 3.9)
    end = t.rstrip("\"'”’)").rstrip()
    if end.endswith("?"):
        return f"a question: {t[:60]!r}"
    if end.endswith("!"):
        return f"a shout: {t[:60]!r}"
    low = f" {re.sub(r'[^a-z0-9 -]', ' ', t.lower())} "
    for w in STRONG_WORDS:
        if f" {w} " in low:
            return f"a word of heat ({w}): {t[:60]!r}"
    return ""


def cold_record(acct, slot: dict) -> Tuple[Optional[dict], str]:
    from . import prehook as PH
    return PH.cold_record(acct, slot)


def own_start(rec: Optional[dict]) -> float:
    """Where the clip's own first seconds begin: after the engine's cold open when it made one."""
    if isinstance(rec, dict) and "skip" not in rec:
        try:
            return max(0.0, float(rec.get("d") or 0.0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _slate_clip(acct, slot: dict) -> dict:
    from . import vscard as VS
    job = VS.slot_job(slot, acct)
    if not job:
        return {}
    try:
        sl = json.loads((VS.JOBS / job / "slate.json").read_text())
    except (OSError, ValueError):
        return {}
    return next((c for c in sl.get("clips") or [] if c.get("slug") == slot.get("slug")), {}) or {}


def fight(acct, slot: dict) -> str:
    """Why the clip is two people clashing ('' when it is not): its VS card, a two-person look, or its own words
    (the Daily 8 board's fight finder: 4+ questions, clashes or dodges inside a minute; a second person need not be
    plated for it to be a fight)."""
    if slot.get("vs_card"):
        return "it opens on its VS card"
    look = str(slot.get("format") or "").lower()
    c = _slate_clip(acct, slot)
    if look in FIGHT_LOOKS or str(c.get("template") or "").lower() in FIGHT_LOOKS \
            or (c.get("framing") or {}).get("where") == "question":
        return "a two-person FACE-OFF"
    clip = slot.get("clip") or {}
    if clip.get("path"):
        from . import daily8 as D
        try:
            m = D.moments(D.cues(Path(clip["path"]).with_suffix(".srt").read_text(errors="replace")))
        except OSError:
            m = {}
        if (m or {}).get("fights"):
            return "its own words are a fight (back and forth, questions pressed)"
    return ""


SHOUT_SAID = re.compile(r"the shout comes at \+([\d.]+)s")


def shout_open(rec: Optional[dict]) -> str:
    """Why the clip opens on a shout ('' when it does not): the engine opened it on the OUTBURST's shout, or left it
    alone because the shout lands inside the first seconds anyway."""
    if not isinstance(rec, dict):
        return ""
    if rec.get("from") == SHOUT_FROM:
        return "it opens on its shout"
    m = SHOUT_SAID.search(str(rec.get("skip") or ""))
    if m and float(m.group(1)) <= CALM_FOR:
        return f"its shout lands at +{float(m.group(1)):.1f}s"
    return ""


def calm(acct, slot: dict, rec: Optional[dict] = None, have_rec: bool = False) -> Tuple[bool, str]:
    """(True, why) when the clip starts calm: no fight and no strong line in its own first CALM_FOR seconds."""
    why = fight(acct, slot)
    if why:
        return False, why
    if not have_rec:
        rec, _why = cold_record(acct, slot)
    if shout_open(rec):
        return False, shout_open(rec)
    clip = slot.get("clip") or {}
    if not clip.get("path"):
        return False, "the slot holds no clip"
    srt = Path(clip["path"]).with_suffix(".srt")
    got = cues(srt)
    if not got:
        return False, f"no captions beside the clip ({srt.name}): its start cannot be read"
    t0 = own_start(rec)
    for a, b, text in got:
        if b <= t0 or a >= t0 + CALM_FOR:
            continue
        s = strong(text)
        if s:
            return False, f"its own first {CALM_FOR:.0f} s hold {s}"
    return True, f"no fight and no strong line in its own first {CALM_FOR:.0f} s"


def decide(acct, slot: dict) -> Tuple[str, str]:
    """(kind, why): vs | shout | prehook | cold | own, the rule's first yes. Read only."""
    if slot.get("vs_card"):
        return "vs", "two people clash: the VS card, then the punch line"
    rec, _why = cold_record(acct, slot)
    if shout_open(rec):
        return "shout", f"{shout_open(rec)}: nothing goes before it"
    ok, why = calm(acct, slot, rec, have_rec=True)
    if ok:
        return "prehook", f"a calm start ({why}): a pre-hook, then the punch line"
    if isinstance(rec, dict) and "skip" not in rec:
        return "cold", f"the punch line first: {str(rec.get('text') or '')[:60]!r}"
    return "own", f"it opens on its own line ({why})"


def record(acct, slot: dict) -> dict:
    """slot["opener"]: what the clip opens on now, at collect (after the VS card, the test and the fallback)."""
    if slot.get("vs_card"):
        kind, why = "vs", "the VS card, then the punch line"
    elif slot.get("pre_hook"):
        ph = slot["pre_hook"]
        kind = "prehook"
        why = (f"the pre-hook {ph.get('name')}"
               + (" (the opener rule: a calm start)" if ph.get("fallback") else " (the pre-hook test, arm B)"))
    else:
        kind, why = decide(acct, slot)
        if kind == "prehook":          # calm, but no pre-hook went on: the test's control arm, or none fitted
            rec, _w = cold_record(acct, slot)
            kind = "cold" if isinstance(rec, dict) and "skip" not in rec else "own"
            why = f"a calm start with no pre-hook on it (the test's control arm, or none fitted): {why}"
    rec = {"kind": kind, "why": why[:300]}
    slot["opener"] = rec
    B.note(slot, f"opener: {kind} ({why[:160]})")
    return rec
