"""A clip's extras: its first comment on each platform, the Instagram poll and the trial-reel flag. Signed with the clip.

    state/<account>/extras/<YYYY-MM-DD>/<HHMM>.json      written json.dumps(..., indent=1, sort_keys=True)

    {"v": 1, "account": "gaming", "day": "2026-10-20", "slot": "08:45", "clip_sha": "...",
     "first_comment": {"facebook_reels": {"text": "...", "from": "default|edited|task"}},
     "poll": {"instagram_reels": {"options": ["Yes", "No"], "from": "task"}},
     "trial": {"instagram_reels": {"graduation_strategy": "SS_PERFORMANCE", "experiment": "e-0003", "arm": "B"}},
     "experiment": {"e-0003": "B"}, "edited": [{"by", "at", "what"}]}

The slot points at it: slot["extras"] = {"path", "sha256"} (the SHA-256 of the file's bytes). Only then is it part of
what a person signs: board.content_hash, verify and seen_key add it when the pointer is there, so every clip without
extras keeps the signature it always had. It is its own file, never a section in a paste file (copytext switches the
whole file to sections as soon as one header appears, and the YouTube title would go blank).

Written at collect (social.after_take, only when some part that adds to it is switched on), changed only while the
clip waits for Approve (edit, under the board's lock), adopted as it is at Approve (signoff._as_is), and cleared when
collect takes a new clip into the slot or Make again sends it back. After signing, an edit voids the signature: the
poster refuses the clip until it is pulled and approved again. Never in a delivery or drafts folder.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from . import board as B
from . import config as C

V = 1
OUTLETS = ("facebook_reels", "instagram_reels", "youtube_shorts")
SECTIONS = ("first_comment", "poll", "trial", "experiment")
META = ("v", "account", "day", "slot", "clip_sha", "edited")
FROM = ("default", "edited", "task")
CHAR_LIMIT = {"facebook_reels": 8000, "instagram_reels": 2200, "youtube_shorts": 10000}
GRADUATION = ("MANUAL", "SS_PERFORMANCE")
POLL_KEYS = ("option_a", "option_b", "option_c", "option_d")
KEEP_EDITS = 20


# ------------------------------------------------------------------------------------------------------ where
def _slug(acct) -> str:
    return str(getattr(acct, "slug", acct) or "")


def _day(day) -> str:
    return day.isoformat() if isinstance(day, date) else str(day)[:10]


def path(acct, day, slot_key: str) -> Path:
    """state/<slug>/extras/<YYYY-MM-DD>/<HHMM>.json (at call time)."""
    return Path(C.STATE) / _slug(acct) / "extras" / _day(day) / f"{C.normal_slot(slot_key).replace(':', '')}.json"


def _ours(p: Path) -> bool:
    """A file this module may delete: a .json in state/<slug>/extras/<day>/, by its real path."""
    try:
        real = Path(os.path.realpath(p))
        top = Path(os.path.realpath(C.STATE))
        return (real.suffix == ".json" and len(real.parts) >= 4 and real.parts[-3] == "extras"
                and real.parent.parent.parent.parent == top)
    except (OSError, ValueError, TypeError):
        return False


def _pointer(slot: dict) -> dict:
    x = slot.get("extras") if isinstance(slot, dict) else None
    return x if isinstance(x, dict) else {}


# ------------------------------------------------------------------------------------------------------ reading
def read(slot: dict) -> dict:
    """The slot's extras file as it is on disk ({} when there is none, or it is gone or broken)."""
    p = _pointer(slot).get("path")
    if not p:
        return {}
    try:
        data = json.loads(Path(str(p)).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def first_comment(slot: dict, outlet: str) -> str:
    """The first comment for one platform as it is on disk ('' when there is none)."""
    fc = read(slot).get("first_comment")
    ent = fc.get(outlet) if isinstance(fc, dict) else None
    text = ent.get("text") if isinstance(ent, dict) else None
    return text.strip() if isinstance(text, str) else ""


def hash_parts(slot: dict) -> Optional[str]:
    """What board.content_hash adds for the extras: the file's SHA-256, or None when the slot has none (its signature
    is then what it always was)."""
    x = _pointer(slot)
    return str(x["sha256"]) if x.get("sha256") else None


def problems(slot: dict, cached: bool = False) -> List[str]:
    """What board.verify says about the extras: the file gone, or changed since its SHA-256 was taken."""
    x = _pointer(slot)
    if not x.get("path"):
        return []
    p = Path(str(x["path"]))
    if not p.is_file():
        return [f"the first comment or poll file is gone: {p}"]
    if B.sha256_file(p, cached=cached) != x.get("sha256"):
        return ["the first comment or poll was edited after it was collected (Pull it, then approve it again)"]
    return []


def seen_parts(slot: dict) -> Optional[list]:
    """What board.seen_key adds: [path, [size, mtime_ns]] of the file (None when the slot has none)."""
    x = _pointer(slot)
    if not x.get("path"):
        return None
    try:
        st = os.stat(str(x["path"]))
        stat = [st.st_size, st.st_mtime_ns]
    except (OSError, TypeError, ValueError):
        stat = None
    return [x["path"], stat]


# ------------------------------------------------------------------------------------------------------ checking
def check(data: dict) -> Dict[str, object]:
    """The sections as a clean dict (SystemExit naming what is wrong). A first comment with no words is dropped:
    an empty box means no first comment."""
    if not isinstance(data, dict):
        raise SystemExit("extras: a dict of first_comment, poll, trial and experiment")
    odd = sorted(k for k in data if k not in SECTIONS and k not in META)
    if odd:
        raise SystemExit(f"extras: {', '.join(odd)} is not a part; the parts are {', '.join(SECTIONS)}")
    out: Dict[str, object] = {}
    fc = data.get("first_comment") or {}
    if not isinstance(fc, dict):
        raise SystemExit("first_comment: one entry per platform, each {text, from}")
    comments = {}
    for outlet, ent in sorted(fc.items()):
        if outlet not in OUTLETS:
            raise SystemExit(f"first_comment: {outlet!r} is not a platform; they are {', '.join(OUTLETS)}")
        if not isinstance(ent, dict) or not isinstance(ent.get("text", ""), str):
            raise SystemExit(f"first_comment {outlet}: {{text, from}}")
        text = ent.get("text", "").strip()
        if not text:
            continue
        if len(text) > CHAR_LIMIT[outlet]:
            raise SystemExit(f"first_comment {outlet}: {len(text)} characters, over {CHAR_LIMIT[outlet]}")
        how = ent.get("from") or "edited"
        if how not in FROM:
            raise SystemExit(f"first_comment {outlet}: from is one of {', '.join(FROM)}")
        comments[outlet] = {"text": text, "from": how}
    if comments:
        out["first_comment"] = comments
    poll = data.get("poll") or {}
    if not isinstance(poll, dict) or any(k != "instagram_reels" for k in poll):
        raise SystemExit("poll: only instagram_reels takes a poll")
    if poll.get("instagram_reels"):
        ent = poll["instagram_reels"]
        opts = ent.get("options") if isinstance(ent, dict) else None
        if not isinstance(opts, list) or not 2 <= len(opts) <= 4 or not all(isinstance(o, str) for o in opts):
            raise SystemExit("poll: two to four options")
        opts = [o.strip() for o in opts]
        if not all(1 <= len(o) <= 25 for o in opts):
            raise SystemExit("poll: each option is 1 to 25 characters")
        if len(_poll_json(opts)) > 200:
            raise SystemExit("poll: the options are too long together (200 characters at most)")
        how = ent.get("from") or "edited"
        if how not in FROM:
            raise SystemExit(f"poll: from is one of {', '.join(FROM)}")
        out["poll"] = {"instagram_reels": {"options": opts, "from": how}}
    trial = data.get("trial") or {}
    if not isinstance(trial, dict) or any(k != "instagram_reels" for k in trial):
        raise SystemExit("trial: only instagram_reels takes a trial reel")
    if trial.get("instagram_reels"):
        ent = trial["instagram_reels"]
        if not isinstance(ent, dict) or ent.get("graduation_strategy") not in GRADUATION:
            raise SystemExit(f"trial: graduation_strategy is one of {', '.join(GRADUATION)}")
        out["trial"] = {"instagram_reels": {k: str(v) for k, v in sorted(ent.items())
                                            if k in ("graduation_strategy", "experiment", "arm") and v}}
    exp = data.get("experiment") or {}
    if not isinstance(exp, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in exp.items()):
        raise SystemExit("experiment: {experiment id: arm}")
    if exp:
        out["experiment"] = dict(sorted(exp.items()))
    return out


def _poll_json(opts: List[str]) -> str:
    return json.dumps(dict(zip(POLL_KEYS, opts)), separators=(",", ":"))


# ------------------------------------------------------------------------------------------------------ writing
def _held(acct, day) -> bool:
    """True while some open file holds the day board's lock (B.edit): a second, non-blocking lock is refused."""
    lock = B.board_path(acct, date.fromisoformat(_day(day))).with_suffix(".lock")
    try:
        fd = os.open(str(lock), os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _write(acct, day, slot: dict, sections: dict, by: str, what: str) -> None:
    """Write the file whole (a temp file, then a rename) and point the slot at it; nothing at all leaves no file."""
    clean = check(sections)
    if not clean:
        clear(slot)
        return None
    p = path(acct, day, str(slot.get("slot") or ""))
    clip_sha = str((slot.get("clip") or {}).get("sha256") or "")
    old = read(slot)
    edited = [e for e in (old.get("edited") or []) if isinstance(e, dict)] if old.get("clip_sha") == clip_sha else []
    edited.append({"by": str(by or ""), "at": B.utcnow(), "what": str(what or "")})
    data = {"v": V, "account": _slug(acct), "day": _day(day), "slot": str(slot.get("slot") or ""),
            "clip_sha": clip_sha, **clean, "edited": edited[-KEEP_EDITS:]}
    raw = (json.dumps(data, indent=1, sort_keys=True) + "\n").encode()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, p)
    slot["extras"] = {"path": str(p), "sha256": hashlib.sha256(raw).hexdigest()}
    return None


def write_locked(acct, day, slot: dict, data: dict, by: str, what: str) -> None:
    """Write the slot's extras while its board is held (collect's B.edit, or edit below) and the clip waits for
    Approve. `data` holds the sections (first_comment, poll, trial, experiment); the file is replaced by them."""
    if not isinstance(slot, dict) or slot.get("status") != B.READY:
        raise SystemExit("extras are written only for a clip waiting for Approve")
    if not _held(acct, day):
        raise SystemExit("extras are written only while the day board is held (board.edit)")
    _write(acct, day, slot, data, by, what)


@contextlib.contextmanager
def edit(cfg: C.Config, acct: C.Account, day: date, slot_key: str, by: str, what: str) -> Iterator[dict]:
    """Change a READY clip's extras under the board's lock: yields the sections (first_comment, poll, trial,
    experiment) to change in place, then checks and writes them. A signed clip is refused: Pull it first."""
    key = C.normal_slot(slot_key)
    with B.edit(cfg, acct, day, create=False) as board:
        s = (board.get("slots") or {}).get(key)
        if not isinstance(s, dict):
            raise SystemExit(f"{key}: not a slot on this board")
        if s.get("status") in B.SIGNED:
            raise SystemExit("It is signed: Pull it to change its first comment.")
        if s.get("status") != B.READY:
            raise SystemExit(f"{key}: {s.get('status')}, there is no clip waiting for Approve")
        cur = read(s)
        data = {k: json.loads(json.dumps(cur[k])) for k in SECTIONS if k in cur}
        yield data
        _write(acct, day, s, data, by, what)


def adopt(slot: dict) -> List[str]:
    """Before a signature (signoff._as_is): take the extras as they are on disk now, which is what the page shows. An
    edit made before signing becomes part of what is signed; a file that went away, or no longer reads, is dropped
    and the clip is signed without it. The board notes to write."""
    x = _pointer(slot)
    if not x.get("path"):
        return []
    p = Path(str(x["path"]))
    if not p.is_file():
        slot.pop("extras", None)
        return [f"the first comment or poll file was gone ({p}); signed without it"]
    raw = p.read_bytes()
    try:
        data = json.loads(raw.decode())
        check({k: v for k, v in data.items() if k in SECTIONS} if isinstance(data, dict) else data)
    except (ValueError, UnicodeDecodeError, SystemExit) as e:
        slot.pop("extras", None)
        return [f"the first comment or poll file could not be read ({str(e)[:120]}); signed without it"]
    sha = hashlib.sha256(raw).hexdigest()
    if sha == x.get("sha256"):
        return []
    x["sha256"] = sha
    return ["first comment or poll edited after collecting; signed as it is now"]


def clear(slot: dict) -> None:
    """Take the extras off the slot (a new clip, or Make again): the pointer goes, and its file with it when the file
    is one of ours under state/<slug>/extras."""
    x = slot.pop("extras", None) if isinstance(slot, dict) else None
    p = x.get("path") if isinstance(x, dict) else None
    if p and _ours(Path(str(p))):
        try:
            Path(str(p)).unlink()
        except OSError:
            pass
    return None


# ------------------------------------------------------------------------------------------------------ the reel
def ig_form(slot: dict, acct) -> Dict[str, str]:
    """What the Instagram reel's container takes from the extras (direct.ig_post): poll_attachment while the poll
    switch is on, trial_params while trial_reels is on and the trial is an experiment's arm. Only for a clip signed
    with exactly this file: anything that does not match gives {} and the reel goes as it always went."""
    from . import switches as SW
    x = _pointer(slot)
    so = slot.get("signoff") if isinstance(slot, dict) else None
    if not x.get("path") or not isinstance(so, dict) or not so.get("hash"):
        return {}
    poll_on, trial_on = SW.mode(acct, "poll") == "on", SW.mode(acct, "trial_reels") == "on"
    if not (poll_on or trial_on):
        return {}
    try:
        raw = Path(str(x["path"])).read_bytes()
        if hashlib.sha256(raw).hexdigest() != x.get("sha256") or so.get("hash") != B.content_hash(slot):
            return {}
        data = json.loads(raw.decode())
        if not isinstance(data, dict) or data.get("account") != _slug(acct) or \
                data.get("slot") != str(slot.get("slot") or ""):
            return {}
        clean = check({k: v for k, v in data.items() if k in SECTIONS})
    except (OSError, ValueError, UnicodeDecodeError, KeyError, SystemExit):
        return {}
    form: Dict[str, str] = {}
    poll = (clean.get("poll") or {}).get("instagram_reels") if poll_on else None
    if poll:
        form["poll_attachment"] = _poll_json(poll["options"])
    trial = (clean.get("trial") or {}).get("instagram_reels") if trial_on else None
    if trial and trial.get("experiment"):
        form["trial_params"] = json.dumps({"graduation_strategy": trial["graduation_strategy"]},
                                          separators=(",", ":"))
    return form
