"""The drop folder: drag a finished clip into an account's folder and it takes that account's next open slot.

    ~/Desktop/Clip Drop/Gaming/        a video here (.mp4/.mov), optionally a .txt of the same name
    ~/Desktop/Clip Drop/Politics/   (the folder is `inbox_root` in config/accounts.json; its names before
                                           are OLD_NAMES, which doctor still looks for)

    factory.py inbox          (launchd runs it every 5 minutes, a plain script, no AI)

A drop lands in the earliest open slot at least 15 minutes away (today, tomorrow or the day after) as a READY clip
and waits for one click on Approve in Clip Review, like every other clip: nothing posts before the owner signs.
That is `"inbox_auto_sign": false` (the owner's call, 2026-10-17), and also what happens with no setting or a config
that cannot be read. Only `"inbox_auto_sign": true` makes the drop itself the sign-off: signed by the first name on
`approvers` and scheduled, exactly as pressing Approve would. Even then the drop signs only what was dropped: a
clip that collect's social parts gave a first comment, poll, experiment arm or trial reel waits for Approve, so
the page shows those before anything signs them. The length rule holds either way (outside the
account's seconds, it is refused). The source file moves to `Taken/` (or `Refused/`, with the reason beside it), so
the folder only ever shows what is still waiting. The clip bots never drop clips here.

Captions: `<clip name>.txt` beside the video, first line = title, the rest = the caption (the gaming shape), used
for every platform. No .txt: the file name becomes the title and the caption.

A copy of a clip already taken is refused: iCloud makes "NAME 2.mp4" beside a file on the Desktop, and a copy would
post the same clip again at the next slot. One run at a time holds state/inbox.lock. Moving the folder
(deploy/rename.sh in the audit kit) holds it too, so no run recreates the old folder halfway through the move.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from . import board as B
from . import collect as K
from . import config as C
from . import outlets as O

VIDEO = {".mp4", ".mov", ".m4v"}
SETTLE_SECONDS = 30            # a file still being copied in is left for the next run
LEAD = timedelta(minutes=15)   # never take a slot this close
# The folder's READ ME.txt, rewritten by every run when it differs. {lengths} and {drafts} come from the config.
README = """Drag a finished vertical clip into an account's folder. The Mac picks it up within 5 minutes.

- It takes that account's next open posting slot (07:00, 08:45 ... 23:00, at least 15 minutes from now).
- It waits in that slot as a ready clip. Open Clip Review and press Approve: nothing posts before you do.
- Captions: put a .txt with the same name beside the video. First line = title, the rest = caption.
  With no .txt, the file name is used.
- Length: {lengths}. Anything outside it goes to Refused/ with the reason.
- After pickup the file moves to Taken/ (renamed with its date and slot).
- Which platforms post by themselves depends on what is connected (factory.py doctor). The rest land in that
  day's Ready to post folders in {drafts}.
- A copy of a clip already taken (iCloud's "NAME 2.mp4") goes to Refused/, so it never posts twice.
- This folder is yours. The clip bots never put anything here.
"""
APPROVE_LINE = "- It waits in that slot as a ready clip. Open Clip Review and press Approve: nothing posts before you do."
SIGNS_LINE = "- Dropping it here counts as your sign-off. It is approved and scheduled for you."
README_READY = README                                   # the name older code used for the Approve version
README_SIGNS = README.replace(APPROVE_LINE, SIGNS_LINE)  # only with "inbox_auto_sign": true
AUTO_LINE = ("- It waits in that slot as a ready clip. It signs itself {lead:g} minutes before the slot (the automatic "
             "sign-off) unless you Approve, Decline or Make again first in Clip Review.")


def _added_extras(slot: dict) -> str:
    """What collect's social parts added to a dropped clip (social.after_take: a default first comment, an
    experiment's arm, a trial reel, a poll), in words; '' when nothing. The drop signs only what the person dropped:
    a slot carrying any of these waits for Approve, where the page shows them before they are signed."""
    x = slot.get("extras") if isinstance(slot, dict) else None
    if not (isinstance(x, dict) and x.get("path")):
        return ""
    from . import extras as EX
    data = EX.read(slot)
    words = {"first_comment": "a first comment", "poll": "a poll", "trial": "a trial reel",
             "experiment": "an experiment arm"}
    have = [w for k, w in words.items() if data.get(k)]
    return " and ".join(have) if have else "a first comment or poll file"


def auto_sign(cfg: C.Config) -> bool:
    """config/accounts.json "inbox_auto_sign": true = a drop is the sign-off (the old way). false, no setting, or a
    config that cannot be read = a drop waits for Approve (the owner's call of 2026-10-17; never sign by accident)."""
    try:
        return json.loads(Path(cfg.path).read_text()).get("inbox_auto_sign", False) is True
    except (OSError, ValueError, AttributeError):
        return False


def readme(cfg: C.Config) -> str:
    """The folder's READ ME.txt: what a drop does under this config, with each account's length band."""
    lengths = ", ".join(f"{a.name} {a.min_seconds:g} to {a.max_seconds:g} seconds" for a in drop_accounts(cfg))
    if auto_sign(cfg):
        return README_SIGNS.format(lengths=lengths, drafts=_short(cfg.drafts_root))
    from . import autosign as A          # a drop is a ready clip like any other: it signs itself when that is on
    st = [A.settings(cfg, a) for a in drop_accounts(cfg)]
    if st and all(x["on"] for x in st):
        return README.replace(APPROVE_LINE, AUTO_LINE.format(lead=st[0]["lead_minutes"])).format(
            lengths=lengths, drafts=_short(cfg.drafts_root))
    return README.format(lengths=lengths, drafts=_short(cfg.drafts_root))


def root(cfg: C.Config) -> Path:
    return cfg.inbox_root


def drop_accounts(cfg: C.Config) -> List[C.Account]:
    """The accounts that take drops: every account here but one whose block says "drop_folder": false (Lab,
    2026-10-20: a finished clip dropped in would skip its test bank and the truth rule). No folder is made for it, none
    is read, and its length is not in the READ ME."""
    return [a for a in cfg.accounts.values() if (a.raw or {}).get("drop_folder", True) is not False]


# the folder's names before this one (it was renamed after the 10-17 audit): doctor says when a folder by one of
# them holds a clip, because a clip there is never taken
OLD_NAMES = ("Hermes Post Queue",)
LOCK = "inbox.lock"      # in the state folder: one run at a time; deploy/rename.sh holds it while the folder moves


def _short(p: Path) -> str:
    home, s = str(Path.home()), str(p)
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


def _waiting(folder: Path) -> Tuple[int, int]:
    """(clips in its account folders that no run has taken yet, clips at its top, which no run ever takes)."""
    try:
        subs = [d for d in folder.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except OSError:
        return 0, 0
    return sum(len(_videos(d)) for d in subs if d.name not in ("Taken", "Refused")), len(_videos(folder))


COPY_DIR = re.compile(r"^(?P<name>.+?)(?: [2-9])?$")      # a folder, or iCloud's copy of it ("Clip Drop 2")


def _dirs(place: Path) -> List[Path]:
    try:
        return [p for p in place.iterdir() if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return []


def strays(cfg: C.Config) -> List[Path]:
    """Other folders named like the drop folder, by an old name, or iCloud's copy of one ("Clip Drop 2"), where a
    Finder drag, iCloud or the rename may have left one: anywhere on the Desktop up to two folders down (a drag into
    Clipping, or into a dated folder like "Gaming Run 10.17.2026"), beside and inside the account folders, and in
    Clip Drafts. Never the live one, nor a link to it. About 0.2 s on this Desktop."""
    r = root(cfg)
    names = {r.name, *OLD_NAMES}
    try:
        live = r.resolve()
    except OSError:
        live = r
    desk = Path.home() / "Desktop"
    places = {desk, r.parent, cfg.drafts_root}
    for acct in cfg.accounts.values():
        places |= {acct.account_dir, acct.account_dir.parent}
    for d1 in _dirs(desk):
        places.add(d1)
        places |= set(_dirs(d1))
    out, seen = [], {live}          # by where it really is: a Desktop link to an account folder shows it twice
    for place in sorted(places):
        for p in _dirs(place):
            m = COPY_DIR.match(p.name)
            if not m or m.group("name") not in names:
                continue
            try:
                real = p.resolve()
            except OSError:
                continue
            if real not in seen:
                seen.add(real)
                out.append(p)
    return sorted(out)


def check(cfg: C.Config) -> List[Tuple[bool, str, str]]:
    """doctor's lines (good, text, fix): the drop folder is there with a folder per account, what a drop does, and
    no other copy of it holds a clip that will never be taken."""
    r = root(cfg)
    how = "a drop is signed for you (inbox_auto_sign)" if auto_sign(cfg) else "a drop waits for Approve in Clip Review"
    if not r.is_dir():
        out = [(False, f"drop folder {_short(r)} is not there",
                "the inbox job makes it within 5 minutes (factory.py inbox); if it was dragged away, move it back")]
    else:
        missing = [a.name for a in drop_accounts(cfg) if not (r / a.name).is_dir()]
        n, top = _waiting(r)
        text = f"drop folder {_short(r)}: {how}" + (f"; {n} clip(s) waiting for the next run" if n else "")
        text += f"; no folder for {', '.join(missing)}" if missing else ""
        text += f"; {top} clip(s) at its top, outside an account folder (never taken)" if top else ""
        out = [(not missing and not top, text, f"put each clip in its account's folder, {_short(r)}/<Account>/"
                if top else "factory.py inbox (makes the account folders)")]
    for p in strays(cfg):
        n, top = _waiting(p)
        out.append((n + top == 0, f"old copy {_short(p)}: " + (f"{n + top} clip(s) in it that will never be taken"
                    if n + top else "nothing waiting in it (park it when you tidy)"),
                    f"move each clip into {_short(r)}/<Account>/, then park the old folder"))
    return out


def ensure(cfg: C.Config) -> Path:
    r = root(cfg)
    for acct in drop_accounts(cfg):
        (r / acct.name).mkdir(parents=True, exist_ok=True)
    rd = r / "READ ME.txt"
    text = readme(cfg)
    try:
        if not rd.exists() or rd.read_text() != text:
            rd.write_text(text)
    except OSError:
        pass
    return r


COPY_NAME = re.compile(r"^(?P<stem>.+) (?P<n>[2-9])(?P<ext>\.[A-Za-z0-9]+)$")    # iCloud: "NAME 2.mp4" .. " 9"


def _copy_of_taken(folder: Path, v: Path) -> str:
    """The name of the taken file this drop is a copy of ('' when it is not one): iCloud's "NAME 2.mp4" when a
    NAME.mp4 was already taken from this folder. Only that exact name counts: "clip 2.mp4" is not a copy of a taken
    "my clip.mp4", and "rally 2026.mp4" is not one of "rally.mp4" (2026-10-17 review)."""
    m = COPY_NAME.match(v.name)
    if not m:
        return ""
    orig = (m.group("stem") + m.group("ext")).casefold()
    try:
        done = [p.name for p in (folder / "Taken").iterdir() if p.is_file()]
    except OSError:
        return ""
    # Taken/ names are "<date> <HHMM> <name>"; the Mac's disk ignores case, so "X 2.MOV" is a copy of "x.mov"
    return next((n for n in done if len(n.split(" ", 2)) == 3 and n.split(" ", 2)[2].casefold() == orig), "")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "clip"


def _next_slot(cfg: C.Config, acct: C.Account, now: datetime):
    for d in range(3):
        day = (now + timedelta(days=d)).date()
        with B.edit(cfg, acct, day) as bd:          # creates the day's board if nobody has yet
            pass
        free = [s for s in sorted(bd["slots"].values(), key=lambda s: s["slot"])
                if s["status"] in (B.OPEN, B.FAILED) and datetime.fromisoformat(s["publish_at"]) >= now + LEAD]
        # a spare slot (16 to 20) only once no base slot is left that day (2026-10-21)
        pick = [s for s in free if not B.is_spare(s)] or free
        if pick:
            return day, pick[0]["slot"]
    return None, None


def _move(src: Path, dest_dir: Path, name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    shutil.move(str(src), dest)
    return dest


def _videos(folder: Path) -> List[Path]:
    """The clips waiting in a drop folder, oldest first. The Desktop is iCloud's: a listing can be interrupted while a
    file is still arriving (EINTR, 2026-10-17), so it is tried again, then left for the next run."""
    for attempt in range(3):
        try:
            return sorted((p for p in folder.iterdir()
                           if p.is_file() and p.suffix.lower() in VIDEO and not p.name.startswith(".")),
                          key=lambda p: p.stat().st_mtime)
        except InterruptedError:
            time.sleep(0.5 * (attempt + 1))
        except FileNotFoundError:
            return []
    return []


def take(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    """One run at a time (state/inbox.lock). A run that finds the lock held (another run, or deploy/rename.sh moving
    the folder) does nothing, not even make the folder again, and the next run looks again."""
    lock = C.STATE / LOCK
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return ["  the drop folder is busy (another inbox run, or the folder is being moved): the next run looks again"]
        return _take(cfg, now)


def _take(cfg: C.Config, now: Optional[datetime] = None) -> List[str]:
    from . import poster as P
    now = now or cfg.now()
    r = ensure(cfg)
    signs = auto_sign(cfg)
    if signs and not cfg.approvers:
        return [f"  no approvers in {cfg.path}: nobody to sign dropped clips"]
    who = cfg.approvers[0] if cfg.approvers else ""
    lines: List[str] = []
    taken = 0
    signs_all, who_all = signs, who
    for acct in drop_accounts(cfg):
        from . import accounts as AC        # a drop is a sign-off only for an account the owner signs (accounts.py)
        signs = signs_all and AC.signs_here(cfg, acct)
        who = next((x for x in AC.approvers(cfg, acct) if x.lower() in {y.lower() for y in cfg.approvers}), who_all)
        folder = r / acct.name
        vids = _videos(folder)
        for v in vids:
            if time.time() - v.stat().st_mtime < SETTLE_SECONDS or v.stat().st_size == 0:
                continue
            txt = v.with_suffix(".txt")
            dup = _copy_of_taken(folder, v)
            if dup:
                _move(v, folder / "Refused", v.name)
                if txt.exists():
                    _move(txt, folder / "Refused", txt.name)
                (folder / "Refused" / f"{v.stem} - why.txt").write_text(
                    f"a copy of Taken/{dup}, which was already taken: it would post the same clip twice. "
                    f"If it is a different clip, rename it (no ' 2' at the end) and drop it in again.\n")
                lines.append(f"  {acct.name} {v.name}: REFUSED, a copy of Taken/{dup}")
                continue
            secs = K.probe_seconds(v)
            reason = ("could not read the video" if secs is None else
                      f"{secs:.1f}s is over the {acct.max_seconds:.0f}s limit" if secs > acct.max_seconds + 0.05 else
                      f"{secs:.1f}s is under the {acct.min_seconds:.0f}s floor" if secs < acct.min_seconds - 0.05 else "")
            if reason:
                _move(v, folder / "Refused", v.name)
                if txt.exists():
                    _move(txt, folder / "Refused", txt.name)
                (folder / "Refused" / f"{v.stem} - why.txt").write_text(reason + "\n")
                lines.append(f"  {acct.name} {v.name}: REFUSED, {reason}")
                continue
            day, slot = _next_slot(cfg, acct, now)
            if not day:
                lines.append(f"  {acct.name} {v.name}: waiting, no open slot in the next three days")
                continue
            words = txt.read_text() if txt.exists() and txt.read_text().strip() else \
                f"{v.stem.replace('_', ' ').strip()}\n\n{v.stem.replace('_', ' ').strip()}\n"
            hh = slot.replace(":", "")
            stage = folder / ".staging" / f"{hh} {_slug(v.stem)}"
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir(parents=True)
            shutil.copy2(v, stage / v.name)
            for name in {n for names in K.COPY_FILES.values() for n in names[:1]}:   # one paste file per platform
                (stage / name).write_text(words)
            with B.edit(cfg, acct, day, create=False) as bd:
                s = bd["slots"][slot]
                if s["status"] not in (B.OPEN, B.FAILED):     # filled by the maker since _next_slot looked
                    shutil.rmtree(stage, ignore_errors=True)
                    lines.append(f"  {acct.name} {v.name}: {slot} was just filled; the next run takes the next slot")
                    continue
                msg = K._take(cfg, acct, s, stage, K.drafts_dir(cfg, acct, day))
                signed, added = False, ""
                if s["status"] == B.READY:
                    s["source"] = "drop folder"
                    added = _added_extras(s) if signs else ""
                    if signs and not added:
                        s["signoff"] = {"by": who, "at": B.utcnow(), "hash": B.content_hash(s)}
                        s["status"] = B.APPROVED
                        signed = True
                        B.note(s, f"dropped into {r.name}/{folder.name}/ and signed by {who} (inbox_auto_sign: the drop is the sign-off)")
                    elif added:
                        B.note(s, f"dropped into {r.name}/{folder.name}/: NOT signed by the drop, the factory added "
                                  f"{added} that nobody has seen; waits for Approve in Clip Review")
                    else:
                        B.note(s, f"dropped into {r.name}/{folder.name}/: waits for Approve in Clip Review")
            shutil.rmtree(stage, ignore_errors=True)
            if "ready" not in msg:
                lines.append(f"  {acct.name} {v.name}: {msg.strip()}")
                continue
            out = O.hand_off(cfg, acct, day) if signed else []
            moved = _move(v, folder / "Taken", f"{day} {hh} {v.name}")
            if txt.exists():
                _move(txt, folder / "Taken", f"{day} {hh} {txt.name}")
            if signed:
                lines.append(f"  {acct.name} {v.name}: goes out {day} at {slot} ({secs:.0f}s), file moved to Taken/{moved.name}")
                lines += [f"    {ln.strip()}" for ln in out if "NOT scheduled" in ln]
                taken += 1
            else:
                lines.append(f"  {acct.name} {v.name}: ready to approve for {day} at {slot} ({secs:.0f}s) in Clip Review, "
                             f"file moved to Taken/{moved.name}"
                             + (f" (not signed by the drop: it carries {added} the factory added)" if added else ""))
    if taken:
        P.kick(cfg)     # YouTube uploads it now (public at the slot), not at the next 10-minute tick
    return lines
