"""The day board: one JSON file per account per posting day, holding every slot and what happened to it.

    state/<account>/<YYYY-MM-DD>.json

A slot moves   open -> making -> ready -> approved -> scheduled      (Approve signs and schedules in one click)
                                      \\-> declined        scheduled -> ready   (pulled before it went out)
               open -> refused | failed      (a rule said no, or the maker broke; said why in `notes`)

What went out where is in `slot["outlets"][platform]`; the slot stays `scheduled` after it posts.

Every write happens under an exclusive lock and is atomic (write a temp file, rename), so the nightly worker, the
review page and a person at the command line can all touch one board safely.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from . import clipformats as CF
from . import config as C

_DAY_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

OPEN, MAKING, READY, APPROVED, DECLINED, SCHEDULED, REFUSED, FAILED = (
    "open", "making", "ready", "approved", "declined", "scheduled", "refused", "failed")
STATUSES = (OPEN, MAKING, READY, APPROVED, DECLINED, SCHEDULED, REFUSED, FAILED)
# what a person can still act on, and what the maker may (re)fill
SIGNABLE = (READY,)
FILLABLE = (OPEN, MAKING, FAILED)
# signed by a person: it goes out (approved is only the moment before it is scheduled)
SIGNED = (APPROVED, SCHEDULED)


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def board_path(acct: C.Account, day: date) -> Path:
    return C.STATE / acct.slug / f"{day.isoformat()}.json"


def new_board(cfg: C.Config, acct: C.Account, day: date) -> dict:
    slots = {}
    fmts = acct.formats or ["clip"]
    for i, s in enumerate(acct.slots_for(day)):     # a dated "volume" rule, else active_slots()
        slots[s] = {
            "slot": s,
            "publish_at": cfg.slot_time(day, s).isoformat(),
            # the Politics's night run picks each clip's segment from its moment; collect writes the one it took
            "format": "any" if acct.maker == "politics" else fmts[i % len(fmts)],
            "status": OPEN,
            "notes": [],
        }
        moment = CF.board_moment(acct, s)    # Stunts' formats (clipformats.py): the kind of clip the slot posts
        if moment:
            slots[s]["moment"] = moment
    spares = acct.spare_slots_for(day)
    for s in spares:
        # slots 16 to 20 (config.Account.spare_slots): filled only once every base slot holds a clip
        slots[s] = {
            "slot": s,
            "publish_at": cfg.slot_time(day, s).isoformat(),
            "format": "any" if acct.maker == "politics" else fmts[0],
            "status": OPEN,
            "notes": [],
            "spare": True,
        }
    if spares:
        slots = dict(sorted(slots.items()))     # the spares sit among the base slots in time order
    return {"account": acct.slug, "name": acct.name, "date": day.isoformat(), "created_at": utcnow(), "slots": slots}


def is_spare(slot: dict) -> bool:
    return bool((slot or {}).get("spare"))


def base_slots(board: dict) -> List[dict]:
    """The day's base slots in time order (every slot but the spares)."""
    return [s for _k, s in sorted(((board or {}).get("slots") or {}).items()) if not is_spare(s)]


def spares_may_fill(board: dict) -> bool:
    """A spare slot takes a clip only once every base slot holds one (none is open, making or failed)."""
    return not any(s.get("status") in FILLABLE for s in base_slots(board))


def empty_spare(slot: dict) -> bool:
    """A spare slot with nothing made in it (open, being made, failed or refused). An empty spare is fine: the 06:30
    notice, Clip Review and its calendar never count it as a slot of the day or list it as not made; a spare that
    holds a clip counts like any slot (2026-10-22)."""
    return is_spare(slot) and (slot or {}).get("status") in (OPEN, MAKING, FAILED, REFUSED)


def read(acct: C.Account, day: date) -> Optional[dict]:
    p = board_path(acct, day)
    return json.loads(p.read_text()) if p.exists() else None


@contextlib.contextmanager
def edit(cfg: C.Config, acct: C.Account, day: date, create: bool = True) -> Iterator[dict]:
    """Lock, read (or create), yield the board for changes, write it back atomically."""
    p = board_path(acct, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        if p.exists():
            board = json.loads(p.read_text())
        elif create:
            board = new_board(cfg, acct, day)
        else:
            raise SystemExit(f"no board for {acct.slug} on {day}: run `factory.py plan {acct.slug} --date {day}`")
        yield board
        board["updated_at"] = utcnow()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(board, indent=1, sort_keys=False))
        os.replace(tmp, p)


def note(slot: dict, text: str) -> None:
    slot.setdefault("notes", []).append(f"{utcnow()} {text}")


def note_once(slot: dict, text: str) -> bool:
    """Note it unless the last note already says it: the poster runs every 10 minutes, so a standing problem is one
    line on the board, not 144 a day. True when it was noted."""
    if last_note(slot) == text:
        return False
    note(slot, text)
    return True


def last_note(slot: dict) -> str:
    """The newest note without its time stamp ('' when there is none)."""
    notes = slot.get("notes") or []
    return notes[-1].split(" ", 1)[-1] if notes else ""


_SHA_SEEN: Dict[tuple, str] = {}


def sha256_file(path: Path, cached: bool = False) -> str:
    """The file's SHA-256. `cached` reuses the last answer while the file's size, time and inode are unchanged: for
    the review page, which re-checks every signed clip each time it draws. The poster always reads the bytes."""
    key = None
    if cached:
        st = os.stat(path)
        key = (str(path), st.st_size, st.st_mtime_ns, st.st_ino)
        if key in _SHA_SEEN:
            return _SHA_SEEN[key]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if key:
        if len(_SHA_SEEN) > 500:
            _SHA_SEEN.clear()
        _SHA_SEEN[key] = h.hexdigest()
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clip_idents(slot: dict) -> List[str]:
    """The SHA-256s a slot's clip is known by: the file it holds and, when the factory changed it after collecting
    (prehook.py), the file as delivered, so a delivery is still recognised as the clip already on the board."""
    clip = slot.get("clip") or {}
    return [h for h in (clip.get("sha256"), clip.get("delivered_sha256")) if h]


def clip_shas(slot: dict) -> List[str]:
    """clip_idents plus every platform cut's, as held and as delivered."""
    out = clip_idents(slot)
    for c in (slot.get("cuts") or {}).values():
        out += [h for h in ((c or {}).get("sha256"), (c or {}).get("delivered_sha256")) if h]
    return out


def content_hash(slot: dict) -> str:
    """What a person signs: the clip's bytes, every platform's words, and the time it goes out (and its own cuts and
    extras file, only when it has them)."""
    parts = {
        "slot": slot["slot"],
        "publish_at": slot["publish_at"],
        "clip": slot.get("clip", {}).get("sha256"),
        "copy": {k: v.get("sha256") for k, v in sorted(slot.get("copy", {}).items())},
    }
    if slot.get("cuts"):    # a platform's own cut is signed with the clip (only then: older signatures stay true)
        parts["cuts"] = {k: v.get("sha256") for k, v in sorted(slot["cuts"].items())}
    x = slot.get("extras")  # its first comment, poll and trial flag (extras.py), the same way: only when there
    if isinstance(x, dict) and x.get("sha256"):
        parts["extras"] = x["sha256"]
    return sha256_text(json.dumps(parts, sort_keys=True))


def verify(slot: dict, cached: bool = False) -> List[str]:
    """Re-hash what is on disk against what was signed. Empty list = it is exactly what the person approved."""
    problems = []
    clip = slot.get("clip", {})
    p = Path(clip.get("path", ""))
    if not p.is_file():
        problems.append(f"the clip is gone: {p}")
    elif sha256_file(p, cached=cached) != clip.get("sha256"):
        problems.append("the clip changed after it was collected")
    for plat, cut in (slot.get("cuts") or {}).items():
        cp = Path(cut.get("path", ""))
        if not cp.is_file():
            problems.append(f"the {plat} cut is gone: {cp}")
        elif sha256_file(cp, cached=cached) != cut.get("sha256"):
            problems.append(f"the {plat} cut changed after it was collected")
    for plat, c in slot.get("copy", {}).items():
        cp = Path(c.get("path", ""))
        if not cp.is_file():
            problems.append(f"{plat} copy is gone: {cp}")
        elif sha256_text(cp.read_text()) != c.get("sha256"):
            problems.append(f"the {plat} copy was edited after it was collected (re-collect, then sign again)")
    x = slot.get("extras")
    if isinstance(x, dict) and x.get("path"):
        xp = Path(str(x["path"]))
        if not xp.is_file():
            problems.append(f"the first comment or poll file is gone: {xp}")
        elif sha256_file(xp, cached=cached) != x.get("sha256"):
            problems.append("the first comment or poll was edited after it was collected (Pull it, then approve it "
                            "again)")
    so = slot.get("signoff")
    if so and so.get("hash") != content_hash(slot):
        problems.append("what is on the board is not what was signed")
    return problems


def seen_key(slot: dict) -> str:
    """What a person sees of a slot on the review page, as it is on disk now: the clip, its cover and each platform's
    words file (the size and time of each). The page sends it back with Approve, which refuses when it differs:
    a clip or its words changed after the page showed them, so the person has not seen what would be signed."""
    def stat(p) -> Optional[list]:
        try:
            st = os.stat(p)
            return [st.st_size, st.st_mtime_ns]
        except (OSError, TypeError, ValueError):
            return None
    clip = (slot.get("clip") or {}).get("path")
    parts = [slot.get("slot"), clip, stat(clip), slot.get("cover"), stat(slot.get("cover")),
             {k: [v.get("path"), stat(v.get("path"))] for k, v in sorted((slot.get("copy") or {}).items())}]
    if slot.get("cuts"):
        parts.append({k: [v.get("path"), stat(v.get("path"))] for k, v in sorted(slot["cuts"].items())})
    x = slot.get("extras")
    if isinstance(x, dict) and x.get("path"):  # the first comment and poll file (extras.py), only when there is one
        parts.append([x["path"], stat(x["path"])])
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def all_boards(acct: C.Account) -> List[Path]:
    """The account's day boards, oldest first. Only YYYY-MM-DD.json: other state sits beside them (the YouTube
    upload count, youtube-uploads.json) and is not a board."""
    d = C.STATE / acct.slug
    return sorted(p for p in d.glob("*.json") if _DAY_FILE.match(p.stem)) if d.exists() else []


def counts(board: dict) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for s in board["slots"].values():
        out[s["status"]] = out.get(s["status"], 0) + 1
    return out
