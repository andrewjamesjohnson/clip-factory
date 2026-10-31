"""What he said about each clip: Approve, Decline or Make again, with the reasons (Wrong name, Framing, Cut ...).

The reason codes are the Reason codes list Andrew picked on 2026-10-20 ("Use the reason-codes list (WRONG NAME,
FRAMING, CUT, AUDIO, LENGTH, REPEAT, INCOMPLETE, TEXT, HOOK, RIGHTS, Other), with gates and hard flags"): one list
for the whole factory. The first six are hard faults (HARD). The gate each code names and the kill list belong to
the Reason codes part; this module only keeps the verdicts the learner reads.

Make again on the review page opens a row of reason chips (CHIPS) and a note; `factory.py decline ... --reasons
hook,framing` does the same from the command line. Each verdict is one line in state/<account>/learn/verdicts.jsonl:

    {"at", "by", "verdict": "approve|decline|remake|pull", "board", "slot", "slug", "clip_sha", "format", "host",
     "job", "source_url", "reasons": ["hook"], "note": ""}

Rows written before 2026-10-20 carry the first list's codes (weak_hook, slow_start ...): check(), normalize() and
words() read them as the codes they became (OLD), so the learner counts one list.

The learner reads them to say which faults cost the most (the morning report's problems), never to change a clip.
A client's clips are not the owner's to learn from: nothing is written for a client account.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional, Sequence

from . import config as C
from . import reasons as R
from . import store

# code -> the chip's words, in the order the chips are drawn: the Reason codes list itself (reasons.CODES, one list
# for the whole factory; its gates and the kill list live there), its labels in the chips' own case ("Wrong name")
REASONS = tuple(c.key for c in R.CODES)
LABELS = {c.key: c.label.capitalize() for c in R.CODES}
HARD = R.HARD
# the first list's codes (2026-10-19, before Andrew's pick) -> the code each became
OLD = {"weak_hook": "hook", "slow_start": "hook", "wrong_person": "wrong-name", "captions": "text", "title": "text",
       "too_long": "length", "too_short": "length", "hard_to_follow": "incomplete", "boring_middle": "other",
       "wrong_moment": "other", "off_topic": "other"}
CHIPS = [[code, LABELS[code]] for code in REASONS]
VERDICTS = ("approve", "decline", "remake", "pull")


def code_of(c: str) -> str:
    """One code as the list spells it: 'WRONG NAME', 'wrong_name' and an old 'wrong_person' all give 'wrong-name';
    '' for anything that is not a reason."""
    c = str(c or "").strip()
    if c in LABELS:
        return c
    if c in OLD:
        return OLD[c]
    k = c.lower().replace("_", "-").replace(" ", "-")
    return k if k in LABELS else ""


def normalize(codes) -> List[str]:
    """Stored codes as today's list (old codes mapped, unknown ones dropped, each once): what the learner counts."""
    out: List[str] = []
    for c in codes or []:
        k = code_of(c)
        if k and k not in out:
            out.append(k)
    return out


def check(codes) -> List[str]:
    """The reason codes as a clean list (each once, in the order given; old codes and spellings such as 'WRONG NAME'
    are read as today's code), or SystemExit naming an unknown one."""
    if codes is None:
        return []
    if isinstance(codes, str):
        codes = [c for c in codes.split(",")]
    if not isinstance(codes, (list, tuple)):
        raise SystemExit("reasons: a list of reason codes")
    out: List[str] = []
    for c in codes:
        if not isinstance(c, str):
            raise SystemExit("reasons: a list of reason codes")
        c = c.strip()
        if not c:
            continue
        k = code_of(c)
        if not k:
            raise SystemExit(f"{c!r} is not a reason; the reasons are: {', '.join(REASONS)}")
        if k not in out:
            out.append(k)
    return out


def words(codes: Sequence[str]) -> str:
    """The reasons in plain words for a note: "hook, framing" (old codes read as the codes they became; never raises)."""
    return ", ".join(LABELS.get(code_of(c), str(c)).lower() for c in (codes or []))


def _path(slug: str):
    return store.path(slug, "learn", "verdicts.jsonl")


def _is_client(acct) -> bool:
    raw = getattr(acct, "raw", None)
    return isinstance(raw, dict) and str(raw.get("owner") or "self") == "client"


def record(cfg: C.Config, acct: C.Account, day, slot: dict, verdict: str, by: str, reasons: Sequence[str] = (),
           note: str = "", now: Optional[datetime] = None) -> Optional[dict]:
    """One verdict row for the clip in this slot (the row written, or None for a client's account)."""
    if verdict not in VERDICTS:
        raise SystemExit(f"verdict: one of {', '.join(VERDICTS)}")
    if _is_client(acct):
        return None
    reasons = check(list(reasons or []))
    day_s = day.isoformat() if isinstance(day, date) else str(day)
    job = ""
    try:                                   # which skill job the clip came from (bench._job_of: GTA slot, Politics cast)
        from . import bench as BN
        job = BN._job_of(acct, slot, BN._sched(acct), day_s)
    except Exception:  # noqa: BLE001  (a verdict is never lost over its job)
        job = str(slot.get("job") or "")
    row = {"at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat(),
           "by": str(by or ""), "verdict": verdict, "board": day_s, "slot": str(slot.get("slot") or ""),
           "slug": str(slot.get("slug") or ""), "clip_sha": str((slot.get("clip") or {}).get("sha256") or ""),
           "format": str(slot.get("format") or ""), "host": str(slot.get("host") or ""), "job": str(job or ""),
           "source_url": str(slot.get("source_url") or ""), "reasons": reasons, "note": str(note or "")}
    try:                                   # the coach's word and average at the moment of his call (judge.py, the loops thesis item 18)
        from . import coach as CO
        card = CO.load(acct, day, str(slot.get("slot") or ""))
        if CO.fresh(card, slot):
            w = (card or {}).get("written") or {}
            sc = [v for v in (w.get("scores") or {}).values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
            row["coach"] = {"word": str(w.get("verdict") or ""), "avg": round(sum(sc) / len(sc), 2) if sc else None}
    except Exception:  # noqa: BLE001  (a verdict is never lost over the coach)
        pass
    store.append(_path(acct.slug), row)
    return row


def rows(acct, since=None) -> List[dict]:
    """The account's verdicts, oldest first (only those at or after `since`, when given)."""
    return store.rows(_path(str(getattr(acct, "slug", acct))), since=since)
