"""One moment, one slot a day (2026-10-22, LESSONS "Build next" row 7).

The hourly Politics collect took each delivery as it landed. When the night rendered a clip again into its own
day-folder folder (a fixed hook, a new plate), the new render was a new delivery, its own slot already held the
first one, so it went into the NEXT open slot: on 10-22 four stale first renders (07:50, 08:55, 13:35, 17:05) sat
on the board beside their fixed versions (14:50, 18:20, 15:55, 20:40). The night's report said "DO NOT SIGN" in
its middle; all fifteen were approved at 06:06 in one click, and the 07:50 YouTube went up.

What holds now:
  - a delivery folder plus its slug is ONE slot a day. A newer render of a clip REPLACES the unsigned clip in its
    own slot (collect, with a note on the card) and never takes a new slot. `place --replace` does the same for a
    hand placement, and `place` refuses a slug the day already has in another slot.
  - a signed or scheduled clip is never swapped silently: the newer render waits beside it, and its card asks for
    Pull and Approve (the pattern of a platform's late cut). Pull takes it.
  - the night can mark a clip stale ("do not sign"): `factory.py stale <account> --date D --slots S --why "..."`.
    The card shows it big and red; Approve all and the automatic sign-off skip it, so only a one-by-one Approve
    signs it (and that Approve says so on the card's notes).
  - at sign-off a second slot on one day with the same slug, the same delivery folder or the same seconds of the
    same source is refused, and the card names the first slot. Approve all and the automatic sign-off leave a pair
    of unsigned twins for a person to choose between.

Identity: the slug, the delivery folder (source_folder), and the source span (reasons.slot_identity: the video and
the seconds it used). Two clips are the same seconds when more than SAME_SHARE of either's seconds lie inside the
other's (the kill list's own arithmetic, reasons._union_inside): two different clips of one speech never are.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C

SAME_SHARE = 0.5        # seconds shared, as a share of either clip, that make two clips one moment
HOLDS = (B.READY, B.APPROVED, B.SCHEDULED)      # the slots a moment can be on (declined, failed, open: not)


# ------------------------------------------------------------------------------------------------ the stale mark
def stale(s: dict) -> dict:
    """The night's "stale, do not sign" mark on THIS clip ({} when there is none, or the slot holds another clip
    since: a newer render is never stale by inheritance)."""
    m = s.get("stale")
    if not isinstance(m, dict) or not s.get("clip"):
        return {}
    return m if m.get("clip") == (s.get("clip") or {}).get("sha256") else {}


def stale_words(s: dict) -> str:
    m = stale(s)
    if not m:
        return ""
    return f"marked stale by {m.get('by') or 'the night'}: {m.get('why') or 'do not sign'}"


def mark(cfg: C.Config, acct: C.Account, day: date, slots: List[str], why: str, by: str = "the night",
         clear: bool = False) -> List[str]:
    """`factory.py stale`: mark (or clear) the clip in each slot as stale. Anyone may mark (it only holds a clip
    back); a mark names this clip, so a newer render in the slot is free of it."""
    why = " ".join(str(why or "").split())[:300]
    if not clear and not why:
        raise SystemExit("say why it is stale (--why), e.g. \"the first render of 14:50; 14:50 is the fixed one\"")
    lines: List[str] = []
    with B.edit(cfg, acct, day, create=False) as board:
        for k in (C.normal_slot(x) for x in slots):
            s = board["slots"].get(k)
            if s is None:
                lines.append(f"  {k}: not a slot on this board")
                continue
            if clear:
                if s.pop("stale", None):
                    B.note(s, f"stale mark taken off by {by}")
                    lines.append(f"  {k}: stale mark taken off")
                else:
                    lines.append(f"  {k}: not marked stale")
                continue
            if not s.get("clip"):
                lines.append(f"  {k}: holds no clip, nothing to mark")
                continue
            s["stale"] = {"why": why, "by": by, "at": B.utcnow(), "clip": s["clip"].get("sha256")}
            B.note(s, f"marked STALE by {by}: {why}")
            tail = (" It is signed already: Pull it to stop it." if s.get("status") in B.SIGNED else
                    " Approve all and the automatic sign-off skip it; only its own Approve signs it.")
            lines.append(f"  {k}: marked stale ({why}).{tail}")
    return lines


def approved_anyway(s: dict, who: str) -> None:
    """A one-by-one Approve of a clip marked stale: it goes out, and the card's notes say whose call it was."""
    m = stale(s)
    if m:
        B.note(s, f"approved on its own by {who} although {stale_words(s)}")
        s.pop("stale", None)


# ------------------------------------------------------------------------------------------------ one moment
_IDS: Dict[tuple, dict] = {}      # (account, day, slot, clip, folder) -> identity: the review page asks every draw


def identity(cfg: C.Config, acct: C.Account, day: date, s: dict) -> dict:
    """What makes a slot's clip the moment it is: its slug, its delivery folder and its source seconds."""
    key = (acct.slug, str(day), s.get("slot"), (s.get("clip") or {}).get("sha256"), s.get("source_folder"),
           s.get("slug"))
    if key in _IDS:
        return _IDS[key]
    out = {"slug": str(s.get("slug") or ""), "folder": str(s.get("source_folder") or ""), "video": "",
           "segments": []}
    try:
        from . import reasons as R
        ident = R.slot_identity(cfg, acct, day, s)
        out["video"] = str(ident.get("video") or "")
        out["segments"] = list(ident.get("segments") or [])
    except Exception:  # noqa: BLE001  (a slate that cannot be read: the slug and the folder still say it)
        pass
    if len(_IDS) > 400:
        _IDS.clear()
    _IDS[key] = out
    return out


def same(a: dict, b: dict) -> str:
    """Why two identities are one moment ('' when they are not)."""
    if a.get("slug") and a["slug"] == b.get("slug"):
        return f"the same clip ({a['slug']})"
    if a.get("folder") and a["folder"] == b.get("folder"):
        return "the same delivery"
    if a.get("video") and a["video"] == b.get("video") and a.get("segments") and b.get("segments"):
        from . import reasons as R
        share = max(R._union_inside(a["segments"], b["segments"]), R._union_inside(b["segments"], a["segments"]))
        if share > SAME_SHARE:
            return f"the same {share:.0%} of its source"
    return ""


def twins(cfg: C.Config, acct: C.Account, day, board: dict) -> Dict[str, List[Tuple[str, str]]]:
    """slot -> [(another slot holding the same moment, why)], for every slot holding a clip that can still go out.
    `day` may be None: the board's own date."""
    if day is None:
        day = date.fromisoformat(str(board.get("date")))
    slots = {k: s for k, s in (board.get("slots") or {}).items() if s.get("clip") and s.get("status") in HOLDS}
    ids = {k: identity(cfg, acct, day, s) for k, s in slots.items()}
    out: Dict[str, List[Tuple[str, str]]] = {}
    keys = sorted(slots)
    for i, k in enumerate(keys):
        for j in keys[i + 1:]:
            why = same(ids[k], ids[j])
            if why:
                out.setdefault(k, []).append((j, why))
                out.setdefault(j, []).append((k, why))
    return out


def signed_twin(board: dict, pairs: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """The first of a slot's twins that is signed (it went first: this one is the second)."""
    for j, why in pairs:
        if (board["slots"].get(j) or {}).get("status") in B.SIGNED:
            return j, why
    return None


def open_twin(board: dict, pairs: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """A twin still waiting to be signed and not marked stale: a person picks which of the two goes."""
    for j, why in pairs:
        t = board["slots"].get(j) or {}
        if t.get("status") == B.READY and not stale(t):
            return j, why
    return None


def bulk_skip(board: dict, k: str, pairs: List[Tuple[str, str]]) -> str:
    """Why Approve all and the automatic sign-off leave this ready clip for a person ('' when they need not)."""
    s = board["slots"].get(k) or {}
    if stale(s):
        return stale_words(s) + ": only its own Approve signs it"
    first = signed_twin(board, pairs)
    if first:
        return f"{first[1]} is signed on {first[0]} already: one moment posts once a day"
    other = open_twin(board, pairs)
    if other:
        return f"{other[1]} is also on {other[0]}: approve the one you want on its own"
    return ""


def refuse_second(board: dict, pairs: List[Tuple[str, str]]) -> str:
    """Approve's refusal for a second slot of one moment on a day ('' when this one may be signed)."""
    first = signed_twin(board, pairs)
    if first:
        return (f"{first[1]} is already signed on {first[0]}: one moment posts once a day. Decline this one, or "
                f"Pull {first[0]} first")
    return ""


# ------------------------------------------------------------------------------------------------ a newer render
def newer_waiting(s: dict) -> dict:
    """A newer render of a signed clip, waiting beside it for Pull and Approve ({} when none)."""
    n = s.get("newer_render")
    if not isinstance(n, dict) or not n.get("sha256"):
        return {}
    if n.get("sha256") == (s.get("clip") or {}).get("sha256"):
        return {}
    return n


# ------------------------------------------------------------------------------------------------ doctor, notice
def pairs_signed(cfg: C.Config, acct: C.Account, day: date, board: dict) -> List[Tuple[str, str, str]]:
    """(slot, its twin, why) for each pair of slots of one moment that are BOTH signed (each pair once): both go out
    unless one is pulled. Boards from before 2026-10-22 can hold them; sign-off refuses a new one."""
    out = []
    for k, rows in sorted(twins(cfg, acct, day, board).items()):
        for j, why in rows:
            if k < j and all((board["slots"].get(x) or {}).get("status") in B.SIGNED for x in (k, j)):
                out.append((k, j, why))
    return out


def doctor_rows(cfg: C.Config, days: List[date]) -> List[tuple]:
    """(good, text, fix): every moment on two slots of today's and tomorrow's boards, and every clip marked stale."""
    rows = []
    for acct in cfg.accounts.values():
        for day in days:
            bd = B.read(acct, day) or {}
            if not bd.get("slots"):
                continue
            for k, j, why in pairs_signed(cfg, acct, day, bd):
                rows.append((False, f"{acct.name} {day} {k} and {j}: {why}, both signed: both go out", f"Pull one of "
                             f"them in Clip Review (or factory.py pull {acct.slug} --date {day} --slots {j} --by NAME)"))
            for k, s in sorted(bd["slots"].items()):
                if stale(s) and s.get("status") in B.SIGNED:
                    rows.append((False, f"{acct.name} {day} {k}: {stale_words(s)}, and it is signed",
                                 "Pull it in Clip Review, or take the mark off: factory.py stale ... --clear"))
    if not rows:
        rows.append((True, "no clip on two slots of a day, and nothing marked stale is signed", ""))
    return rows
