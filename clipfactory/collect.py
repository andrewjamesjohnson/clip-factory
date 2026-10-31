"""Collect what a skill delivered into the day's drafts.

Both skills deliver the same shape: a dated folder with one `HHMM slug/` subfolder per clip, holding the .mp4, a
cover .jpg, the .srt and one paste file per platform (gaming: youtube/tiktok/reels/facebook.txt; viral-clip-engine:
youtube/tiktok_reach/tiktok_rewards/instagram/facebook/...txt). This reads those, checks the length against the
account's ceiling and the kill list (reasons.py: a span, video or b-roll the owner pulled or declined), copies each
clip into the drafts folder and records hashes on the board.

The drafts folder is what a person reviews:  <drafts_root>/<Account> MM.DD.YYYY/<HHMM slug>/
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C

SLOT_DIR = re.compile(r"^(\d{4})\s+(.+)$")

# platform -> a video of its own beside the main clip, best first: a platform that takes shorter clips than the account
# makes (the Politics's Instagram takes about 60s, its clips run 65-90) gets its own cut, rendered by the nightly run
CUT_FILES = {
    "instagram_reels": ["instagram.mp4", "reels.mp4"],
    "youtube_shorts": ["youtube.mp4"],
    "tiktok": ["tiktok.mp4"],
    "facebook_reels": ["facebook.mp4"],
}
_CUT_NAMES = {n for names in CUT_FILES.values() for n in names}

# platform -> the paste files that may carry its words, best first
COPY_FILES = {
    "youtube_shorts": ["youtube.txt"],
    "tiktok": ["tiktok.txt", "tiktok_reach.txt", "tiktok_rewards.txt"],
    "facebook_reels": ["facebook.txt"],
    "instagram_reels": ["reels.txt", "instagram.txt"],
}


def drafts_dir(cfg: C.Config, acct: C.Account, day: date) -> Path:
    root = C.expand(acct.raw["drafts_root"]) if acct.raw.get("drafts_root") else cfg.drafts_root   # a client's own
    return root / f"{acct.name} {day.month:02d}.{day.day:02d}.{day.year:04d}"


def probe_seconds(path: Path) -> Optional[float]:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60)
        return round(float(json.loads(out.stdout)["format"]["duration"]), 2)
    except Exception:  # noqa: BLE001
        return None


def find_delivered(acct: C.Account, day: date, extra: Optional[List[Path]] = None) -> List[Tuple[str, Path]]:
    """Every `HHMM slug` folder holding an .mp4, from the account's delivery folders (and any extra ones)."""
    found = []
    packed = C.STATE / acct.slug / "delivered" / day.isoformat()   # where the factory packs slots itself (makers.py)
    for root in list(acct.delivered_dirs(day)) + [packed] + list(extra or []):
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            m = SLOT_DIR.match(d.name)
            if d.is_dir() and m and any(p.name not in _CUT_NAMES for p in d.glob("*.mp4")):
                found.append((C.normal_slot(m.group(1)), d))
    return found


# An iCloud Desktop copy ("01-x 2.mp4") sorts BEFORE its original (a space comes before the dot), so it was taken
# as the clip, the cover or the captions (audit FOLD-5, 2026-10-17). An original always wins; a copy only when it
# is all there is.
_ICLOUD_COPY = re.compile(r" \d+$")


def _originals_first(paths) -> List[Path]:
    ps = sorted(paths)
    return [p for p in ps if not _ICLOUD_COPY.search(p.stem)] or ps


def main_clip(folder: Path) -> Path:
    """The delivery's own clip: the first .mp4 that is not a platform's cut, never an iCloud " 2" copy of it."""
    return _originals_first(p for p in folder.glob("*.mp4") if p.name not in _CUT_NAMES)[0]


def cuts_in(folder: Path) -> Dict[str, Path]:
    """platform -> its own cut in this delivery folder (none for most clips)."""
    out: Dict[str, Path] = {}
    for plat, names in CUT_FILES.items():
        p = next((folder / n for n in names if (folder / n).is_file()), None)
        if p:
            out[plat] = p
    return out


def delivery_key(folder: Path) -> str:
    """A delivery is its folder plus its clip's size and time: a re-render into the same folder counts as new. A
    platform's cut is part of it (a cut that arrives later is news); a folder with none keeps the key it always had."""
    st = main_clip(folder).stat()
    key = f"{folder}|{st.st_size}|{int(st.st_mtime)}"
    cuts = cuts_in(folder)
    if cuts:
        key += "|cuts:" + ",".join(f"{plat}:{p.stat().st_size}:{int(p.stat().st_mtime)}" for plat, p in sorted(cuts.items()))
    return key


def pick_copy(folder: Path, platform: str) -> Optional[Path]:
    for name in COPY_FILES.get(platform, [f"{platform}.txt"]):
        p = folder / name
        if p.exists() and p.read_text().strip():
            return p
    return None


def collect(cfg: C.Config, acct: C.Account, day: date, extra: Optional[List[Path]] = None,
            only: Optional[List[str]] = None) -> List[str]:
    """Collect delivered clips into the drafts. Returns one line per thing that happened."""
    lines: List[str] = []
    delivered = find_delivered(acct, day, extra)
    if not delivered:
        return [f"{acct.name} {day}: nothing delivered yet (looked in {', '.join(str(p) for p in acct.delivered_dirs(day))})"]
    out_root = drafts_dir(cfg, acct, day)
    with B.edit(cfg, acct, day) as board, _live(acct, day, board):
        seen = set(board.setdefault("seen", []))   # every delivery ever looked at, whatever came of it
        on_board = {(h, s.get("slug")): s["slot"] for s in board["slots"].values() for h in B.clip_idents(s)}
        refused = refused_on(board)
        for slot_key, folder in delivered:
            key = delivery_key(folder)
            if key in seen:
                continue
            # the same clip under the same slug in a second folder (the GTA skill's run folder copies every clip the
            # factory packed) is never a new delivery
            sha = B.sha256_file(main_clip(folder))
            if sha in refused:
                # a clip refused for its length is never taken, in its slot or any other: a copy of it in another
                # folder, or the same file touched again, is the same clip (_refuse)
                lines.append(f"  {folder.name}: the clip refused for its length at {refused[sha]}; never taken")
                seen.add(key)
                board["seen"].append(key)
                continue
            same = on_board.get((sha, folder.name.split(" ", 1)[1]))
            if same:
                # a platform's cut that arrived after the clip was collected joins it, unless it is signed already:
                # then it waits (not marked seen), and Pull takes it (signoff.pull attaches waiting cuts)
                news = attach_cuts(cfg, acct, board["slots"][same], folder)
                lines.append(f"  {folder.name}: the clip already in {same}; " + (news or "skipped"))
                if "waits" not in news:
                    seen.add(key)
                    board["seen"].append(key)
                continue
            own = own_slot(board, folder) if acct.maker != "gta" else None
            if own is not None and own.get("status") != B.FAILED:
                # ONE DELIVERY, ONE SLOT A DAY (2026-10-22, moments.py): a newer render of a clip the board holds
                # replaces it in its own slot while nobody has signed it, waits beside it once someone has (Pull
                # takes it), and never takes another slot. Before this it went into the next open slot: 10-22 had
                # four stale first renders beside their fixed versions, and all fifteen were approved at once.
                said, done = again(cfg, acct, own, folder, out_root, key, sha)
                lines.append(said)
                for h in B.clip_idents(own):
                    on_board[(h, own.get("slug"))] = own["slot"]
                refused.update(refused_on({"slots": {own["slot"]: own}}))
                if done:
                    seen.add(key)
                    board["seen"].append(key)
                continue
            if own is not None:                 # a failed collect of this very folder: it is that slot's to retry
                if only and own["slot"] not in only:
                    continue
                lines.append(_take(cfg, acct, own, folder, out_root))
                refused.update(refused_on({"slots": {own["slot"]: own}}))
                for h in B.clip_idents(own):
                    on_board[(h, own.get("slug"))] = own["slot"]
                seen.add(key)
                board["seen"].append(key)
                continue
            target = board["slots"].get(slot_key)
            if target is not None and target["status"] not in B.FILLABLE and acct.maker == "gta":
                # GTA cuts on the factory's own clock, so a clip's slot is the one it was cut for (audit FOLD-4): a
                # second clip for a slot that holds one (a hand session's earlier cut left in the Desktop run folder)
                # waits for that slot and never moves to another one
                lines.append(f"  {folder.name}: waits, {slot_key} already holds a clip (a GTA clip only fills the "
                             f"slot it was cut for; `factory.py place` files a hand cut)")
                continue
            if target is None and acct.maker == "gta":
                # GTA, a clip under a time that is not a slot today (the account's day folder still builds September's
                # 09:30 12:00 ...): an OPEN slot only, never one the worker is cutting ('making'), a failed one the
                # retry makes again, or the REACTIONS slot held for its duo (audit FOLD-4)
                target = next((s for s in sorted(board["slots"].values(), key=lambda s: s["slot"])
                               if s["status"] == B.OPEN and s.get("format") != "reactions"), None)
                if target is None:
                    lines.append(f"  {folder.name}: waits, {slot_key} is not a slot today and no slot is open "
                                 f"(`factory.py place` files a hand cut)")
                    continue
            if target is not None and B.is_spare(target) and not B.spares_may_fill(board):
                # a spare slot (16 to 20) fills only once every base slot holds a clip: a clip cut for one
                # while a base slot is still open goes to the base slot (2026-10-21)
                target = None
            if target is None or target["status"] not in B.FILLABLE:
                # the skill used its own slot clock (the Politics's templates sit at 07/12/18/21): take the first
                # empty slot, then the first failed one; a spare slot only once every base slot holds a clip
                by_time = B.base_slots(board)
                if B.spares_may_fill(board):
                    by_time = sorted(board["slots"].values(), key=lambda s: s["slot"])
                target = next((s for s in by_time if s["status"] == B.OPEN), None) or \
                    next((s for s in by_time if s["status"] in B.FILLABLE), None)
                if target is None:
                    lines.append(f"  {folder.name}: every slot is already filled; left where it is")
                    continue
            if only and target["slot"] not in only:
                continue
            lines.append(_take(cfg, acct, target, folder, out_root))
            refused.update(refused_on({"slots": {target["slot"]: target}}))
            for h in B.clip_idents(target):
                on_board[(h, target.get("slug"))] = target["slot"]
            seen.add(key)
            board["seen"].append(key)
    return lines


def own_slot(board: dict, folder: Path) -> Optional[dict]:
    """The slot that already holds this delivery's clip ("one delivery, one slot a day", moments.py): the slot it
    was collected from (its source_folder), else a slot holding a clip under the same slug (a render of the same clip
    into another HHMM folder of the day). Of several, one not marked stale first, then the earliest. A failed collect
    of the folder itself counts (its slot is the one to fill). None when no slot holds it."""
    from . import moments as MO
    slug = folder.name.split(" ", 1)[-1]
    live = (B.READY,) + B.SIGNED + (B.DECLINED,)
    slots = sorted(board["slots"].values(), key=lambda x: str(x.get("slot")))
    groups = ([x for x in slots if x.get("source_folder") == str(folder) and x.get("clip") and x.get("status") in live],
              [x for x in slots if x.get("slug") == slug and x.get("clip") and x.get("status") in live],
              [x for x in slots if x.get("source_folder") == str(folder) and x.get("status") == B.FAILED])
    for g in groups:
        if g:
            return sorted(g, key=lambda x: (bool(MO.stale(x)), str(x.get("slot"))))[0]
    return None


def would_refuse(cfg: C.Config, acct: C.Account, slot: dict, folder: Path, secs: Optional[float]) -> str:
    """Why _take would refuse this delivery for the slot ('' when it would take it): its length, its words, the kill
    list. A newer render that would be refused never takes the place of a clip the slot already holds."""
    if secs is None:
        return f"ffprobe could not read {main_clip(folder).name}"
    if secs > acct.max_seconds + 0.05:
        return f"{secs:.1f}s is over the {acct.max_seconds:.0f}s ceiling"
    if secs < acct.min_seconds - 0.05:
        return f"{secs:.1f}s is under the {acct.min_seconds:.0f}s floor"
    missing = [p for p in acct.platforms if pick_copy(folder, p) is None]
    if missing:
        return f"no words for {', '.join(missing)}"
    killed, why = _killed(acct, slot, folder)
    if killed:
        return f"on the kill list: {why}"
    return ""


def again(cfg: C.Config, acct: C.Account, own: dict, folder: Path, out_root: Path, key: str,
          sha: str) -> Tuple[str, bool]:
    """A newer render of the clip `own` holds: (what happened, whether the delivery is done with). Unsigned: it
    replaces the clip in that slot, noted on the card. Signed or scheduled: it waits beside it (Pull takes it).
    Declined, or one collect would refuse: not taken, and the slot keeps what it has."""
    slug = folder.name.split(" ", 1)[1]
    k = own["slot"]
    secs = probe_seconds(main_clip(folder))
    was = dict(own.get("clip") or {})
    was_s = f"{float(was.get('seconds') or 0):.1f}s"
    if own.get("status") == B.DECLINED:
        B.note_once(own, f"a newer render of this declined clip came in ({folder.name}); not taken: it was declined "
                         f"(Make again asks for a new clip)")
        return f"  {folder.name}: a newer render of {k}'s declined clip; not taken", True
    why = would_refuse(cfg, acct, own, folder, secs)
    if why:
        B.note_once(own, f"a newer render of this clip came in and was not taken ({why}); the slot keeps the clip it "
                         f"has")
        return f"  {folder.name}: a newer render of {k}'s clip, NOT taken ({why}); {k} keeps its clip", True
    if own.get("status") in B.SIGNED:
        if (own.get("newer_render") or {}).get("sha256") != sha:
            own["newer_render"] = {"folder": str(folder), "sha256": sha, "seconds": secs, "at": B.utcnow()}
            B.note(own, f"a newer render of this clip arrived after signing ({secs:.1f}s; the signed one is {was_s}): "
                        f"Pull, watch it, and approve again to take it. Otherwise the signed one goes out")
        return (f"  {folder.name}: a newer render of {k}'s clip waits beside it (the clip is signed: Pull takes it, "
                f"never a new slot)"), False
    said = _take(cfg, acct, own, folder, out_root)
    if own.get("status") == B.READY:
        own["replaced"] = {"at": B.utcnow(), "was_sha256": was.get("sha256"), "was_seconds": was.get("seconds"),
                           "from": str(folder)}
        own.pop("newer_render", None)
        B.note(own, f"replaced by a newer render of the same clip ({secs:.1f}s; the earlier one was {was_s}): it "
                    f"never takes a second slot")
        return f"  {k} {slug}: a newer render replaced the unsigned clip in its own slot ({said.strip()})", True
    return said, True


def take_newer(cfg: C.Config, acct: C.Account, day: date, s: dict) -> str:
    """Pull's part: the newer render that waited beside a signed clip takes its place now ('' when none waits, or
    it is gone or changed). The slot is unsigned by then; the board is the caller's (under its lock)."""
    from . import moments as MO
    n = MO.newer_waiting(s)
    if not n:
        return ""
    folder = Path(str(n.get("folder") or ""))
    try:
        if not folder.is_dir() or B.sha256_file(main_clip(folder)) != n["sha256"]:
            s.pop("newer_render", None)
            return "the newer render that waited is gone or changed since; the slot keeps its clip"
    except (OSError, IndexError):
        s.pop("newer_render", None)
        return "the newer render that waited is gone; the slot keeps its clip"
    said, _ = again(cfg, acct, s, folder, drafts_dir(cfg, acct, day), "", n["sha256"])
    s.pop("newer_render", None)
    return said.strip()


_LIVE: Dict[Tuple[str, date], dict] = {}       # the board a collect holds open: its slots changed in this pass


@contextlib.contextmanager
def _live(acct: C.Account, day: date, board: dict):
    _LIVE[(acct.slug, day)] = board
    try:
        yield
    finally:
        _LIVE.pop((acct.slug, day), None)


def _cut_twin(acct: C.Account, slot: dict, sha: str) -> str:
    """'HH:MM's <platform> cut' when another slot of the same day already holds this file as a cut or as its clip ('' when
    none). 10-23: 13:35 and 15:55 shared one Instagram cut, 17:05 and 20:40 one YouTube cut: each went out twice."""
    try:
        day = datetime.fromisoformat(str(slot.get("publish_at"))).date()
    except (TypeError, ValueError):
        return ""
    bd = _LIVE.get((acct.slug, day)) or B.read(acct, day) or {}
    for k, o in sorted((bd.get("slots") or {}).items()):
        if not isinstance(o, dict) or k == slot.get("slot"):
            continue
        if sha in B.clip_idents(o):
            return f"{k}'s clip"
        for p, c in (o.get("cuts") or {}).items():
            if sha in ((c or {}).get("sha256"), (c or {}).get("delivered_sha256")):
                return f"{k}'s {p} cut"
    return ""


def attach_cuts(cfg: C.Config, acct: C.Account, slot: dict, folder: Path) -> str:
    """Copy the delivery's platform cuts beside the slot's clip and record them ('' when there was nothing new).
    A cut outside its platform's length band is left out, said why. A cut that arrives after the clip was signed joins
    it by itself when it changes nothing that was signed (_late_cut_waits), and the signature takes it in
    (_join_signed); otherwise it waits: Pull the clip and approve it again to take the cut."""
    from . import outlets as O
    said = []
    for plat, src in sorted(cuts_in(folder).items()):
        have = (slot.get("cuts") or {}).get(plat) or {}
        sha = B.sha256_file(src)
        if sha in (have.get("sha256"), have.get("delivered_sha256")):     # delivered: before its pre-hook
            continue
        name = O.NAMES.get(plat, plat)
        signed = slot.get("status") in B.SIGNED
        if signed:
            why = _late_cut_waits(acct, slot, plat, src)
            if why:
                B.note_once(slot, f"a new {name} cut arrived after signing and waits ({why}); Pull and approve again "
                                  "to take it")
                said.append(f"{name} cut waits (the clip is signed)")
                continue
        twin = _cut_twin(acct, slot, sha)
        if twin:
            B.note_once(slot, f"{name} cut left out: the same file as {twin} (it would post twice)")
            said.append(f"{name} cut left out (the same file as {twin})")
            continue
        secs = probe_seconds(src)
        lo, hi = O.band(acct, plat, cut=True)          # the cut's own band (the Politics's YouTube cut: 30-59s)
        if secs is None or not lo - 0.05 <= secs <= hi + 0.05:
            why = "unreadable" if secs is None else f"{secs:.1f}s, outside {name}'s {lo:.0f}-{hi:.0f}s"
            B.note(slot, f"{name} cut left out: {why}")
            said.append(f"{name} cut left out ({why})")
            continue
        dest = Path(slot["folder"]) / f"{plat}.mp4"
        shutil.copy2(src, dest)
        slot.setdefault("cuts", {})[plat] = {"path": str(dest), "sha256": B.sha256_file(dest), "seconds": secs,
                                             "from": src.name}
        words = folder / f"{src.stem}.cut.txt"         # the cut's own words, when the clip's would overclaim
        if words.is_file() and words.read_text().strip() and plat in (slot.get("copy") or {}):
            text = words.read_text()
            wdest = Path(slot["copy"][plat]["path"])
            wdest.write_text(text)
            slot["copy"][plat].update(sha256=B.sha256_text(text), **{"from": words.name})
        B.note(slot, f"{name} cut collected ({secs:.1f}s)")
        said.append(f"{name} cut {secs:.1f}s")
        if slot.get("pre_hook"):                     # a cut that arrives after its clip took the pre-hook gets it too
            from . import prehook as PH
            if PH.apply_cut(acct, slot, plat):
                said[-1] += " (as delivered: no pre-hook fits it)"
        if slot.get("vs_card"):                      # ... and the VS card (vscard.py)
            from . import vscard as VS
            if VS.apply_cut(acct, slot, plat):
                said[-1] += " (as delivered: no room for the VS card)"
        if signed:                                   # last: the signature takes the cut as it will go out
            said[-1] += _join_signed(slot, plat, name)
    return ", ".join(said)


def _late_cut_waits(acct: C.Account, slot: dict, plat: str, src: Path) -> str:
    """Why a platform's cut that arrived after its clip was signed waits for Pull and Approve ('' when it joins the
    signed clip by itself). 2026-10-29 (Andrew: late cuts join a signed clip by themselves): the 06:30 Van Hollen clip
    was signed at 21:31, its Instagram and YouTube cuts came at 23:02 and 00:53 and waited for a Pull nobody made, so
    both platforms gave up at the slot. A late cut joins only when it adds that platform's own file and changes
    nothing that was signed or sent: the clip exactly as signed, no cut of that platform signed already, no words of
    its own, nothing of it out on that platform or on its way. The outlet's "late_cut_joins": false keeps the old way."""
    from . import outlets as O
    name = O.NAMES.get(plat, plat)
    if (acct.outlets.get(plat) or {}).get("late_cut_joins") is False:
        return "late_cut_joins is off for this platform"
    if B.verify(slot):
        return "the clip changed after signing"
    if (slot.get("cuts") or {}).get(plat):
        return "it would replace the cut that was signed"
    words = src.parent / f"{src.stem}.cut.txt"
    if words.is_file() and words.read_text().strip():
        return "it brings its own words"
    rec = (slot.get("outlets") or {}).get(plat) or {}
    if rec.get("post_id") or rec.get("video_id") or rec.get("url"):
        return f"{name} has the clip already"
    if rec.get("attempt"):
        return f"a send to {name} is on its way"
    return ""


def _join_signed(slot: dict, plat: str, name: str) -> str:
    """Sign a late cut in with the clip it was made from: the signature's hash takes the cut and the cut is listed
    under the signoff (who signed, and when, stay as they were). A platform that already gave up for want of this cut
    is told Try again sends it: nothing posts late by itself."""
    so = slot["signoff"]
    so.setdefault("cuts_joined", []).append({"platform": plat, "sha256": slot["cuts"][plat].get("sha256"),
                                             "at": B.utcnow(), "was": so.get("hash")})
    so["hash"] = B.content_hash(slot)
    rec = (slot.get("outlets") or {}).get(plat) or {}
    if rec.get("gave_up") and str(rec.get("why") or "").startswith(f"no {name} cut"):
        rec["why"] = "its cut came after its time: Try again sends it"
        B.note(slot, f"{name} cut joined the signed clip by itself, after {name} had given up: Try again sends it")
        return " (joined the signed clip; Try again sends it)"
    B.note(slot, f"{name} cut joined the signed clip by itself (made from the clip {so.get('by') or 'you'} signed, "
                 "its words unchanged)")
    return " (joined the signed clip)"


def _take(cfg: C.Config, acct: C.Account, slot: dict, folder: Path, out_root: Path) -> str:
    mp4 = main_clip(folder)
    secs = probe_seconds(mp4)
    label = f"  {slot['slot']} {folder.name.split(' ', 1)[1]}"
    slot["source_folder"] = str(folder)
    if secs is None:
        slot["status"] = B.FAILED
        slot.update(fail_kind="collect", failed_at=_failed_at())     # makers.make_passes reads both
        B.note(slot, f"could not read the length of {mp4.name}")
        return f"{label}: FAILED, ffprobe could not read it"
    if secs > acct.max_seconds + 0.05:
        _refuse(slot, mp4, folder, secs, f"{secs:.1f}s is over the {acct.max_seconds:.0f}s ceiling")
        return f"{label}: REFUSED, {secs:.1f}s is over {acct.max_seconds:.0f}s (the slot stays fillable)"
    if secs < acct.min_seconds - 0.05:
        _refuse(slot, mp4, folder, secs, f"{secs:.1f}s is under the {acct.min_seconds:.0f}s floor")
        return f"{label}: REFUSED, {secs:.1f}s is under {acct.min_seconds:.0f}s (the slot stays fillable)"
    missing = [p for p in acct.platforms if pick_copy(folder, p) is None]
    if missing:
        slot["status"] = B.FAILED
        slot.update(fail_kind="collect", failed_at=_failed_at())
        B.note(slot, f"no words for {', '.join(missing)} in {folder}")
        return f"{label}: FAILED, no copy for {', '.join(missing)}"
    killed, why = _killed(acct, slot, folder)
    if killed:
        # the owner pulled or declined this span, this video or this b-roll before (reasons.py): never drafted, like
        # a clip outside the band; the GTA night tries its next candidate (makers.cut_gta_slots reads kill_hit)
        _refuse(slot, mp4, folder, secs, f"on the kill list: {why}")
        slot["kill_hit"] = {"id": killed.get("id"), "candidate": slot.get("pick"), "why": why[:300]}
        return f"{label}: REFUSED, on the kill list: {why} (the slot stays fillable)"

    dest = out_root / f"{slot['slot'].replace(':', '')} {folder.name.split(' ', 1)[1]}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    clip = dest / mp4.name
    shutil.copy2(mp4, clip)
    cover = next(iter(_originals_first(folder.glob("*.jpg"))), None)
    srt = next(iter(_originals_first(p for p in folder.glob("*.srt") if f"{p.stem}.mp4" not in _CUT_NAMES)), None)
    for extra in (cover, srt):
        if extra:
            shutil.copy2(extra, dest / extra.name)
    copy: Dict[str, dict] = {}
    for plat in acct.platforms:
        src = pick_copy(folder, plat)
        assert src is not None
        text = src.read_text()
        out = dest / f"{plat}.txt"
        out.write_text(text)
        copy[plat] = {"path": str(out), "sha256": B.sha256_text(text), "from": src.name}
    slot.update({
        "status": B.READY,
        "slug": folder.name.split(" ", 1)[1],
        "folder": str(dest),
        "clip": {"path": str(clip), "sha256": B.sha256_file(clip), "seconds": secs},
        "cover": str(dest / cover.name) if cover else None,
        "copy": copy,
        "collected_at": B.utcnow(),
    })
    slot.pop("signoff", None)
    slot.pop("cuts", None)
    slot.pop("kill_hit", None)
    slot.pop("pre_hook", None)        # the last clip's (prehook.py): the experiment puts it on this one again
    slot.pop("vs_card", None)         # the last clip's VS card (vscard.py): this one gets its own, or none
    if slot.get("extras"):            # the last clip's first comment and poll (extras.py) never ride on this one
        try:
            from . import extras as EX
            EX.clear(slot)
        except Exception:  # noqa: BLE001
            slot.pop("extras", None)
    slot.pop("fail_kind", None)       # an earlier failure of the slot (makers._fail, _refuse): it holds a clip now
    slot.pop("failed_at", None)
    try:
        set_segment(acct, slot)
    except Exception:  # noqa: BLE001  (a label: never stops a collect)
        pass
    try:
        slot.update(source_of(acct, folder, str(slot.get("publish_at") or "")[:10]))   # the Stats tab's Source
    except Exception:  # noqa: BLE001  (a broken source file or URL never stops a collect: the slot keeps what it had)
        pass
    if acct.maker == "gta" and slot.get("format") != "reactions":   # a REACTIONS duo is many creators: place says so
        try:
            own = gta_provenance(acct, folder, str(slot.get("publish_at") or "")[:10])
            if own.get("job") and slot.get("job") and own["job"] != slot["job"]:
                B.note(slot, f"credited to the clip's own job {own['job']} ({own.get('host', '?')}, "
                             f"{own.get('format', slot.get('format', '?'))}), not the plan's {slot['job']}")
                # the picks were the plan's job's: makers._job_history reads them by job, so under the clip's own job
                # they would mark its candidates tried and its spans cut (audit gta-yield, 10-18)
                for k in ("picks_tried", "pick", "pick_span", "question_written"):
                    slot.pop(k, None)
            if own.get("source_url") and slot.get("source_url") and own["source_url"] != slot["source_url"]:
                was = [u for u in slot.get("sources_tried") or [] if u]      # the plan's source stays tried that day
                slot["sources_tried"] = (was + ([slot["source_url"]] if slot["source_url"] not in was else []))[-20:]
            slot.update(own)
        except Exception:  # noqa: BLE001  (a label: never stops a collect)
            pass
    B.note(slot, f"collected from {folder} ({secs:.1f}s)")
    confirm = attach_cast_check(slot, folder, dest)
    cuts = attach_cuts(cfg, acct, slot, folder)
    if acct.maker == "politics":
        try:                          # THE SEQUEL (Daily 8 step 4, 2026-10-29): a Part 2's slate says which part it
            from . import opener as OP    # is; the VS card says it across its top, the Daily 8 board reads it
            seq = (OP._slate_clip(acct, slot) or {}).get("sequel")
            if isinstance(seq, dict) and int(seq.get("part") or 0) >= 2:
                slot["sequel"] = {"part": int(seq["part"]), "of": str(seq.get("of") or "")}
        except Exception as e:  # noqa: BLE001  (a label: never stops a collect)
            B.note(slot, f"no sequel record: {e}"[:300])
        try:                          # THE KEPT MASTERS (masters.py, 2026-10-29): the work folder for 14 days, so a
            from . import masters as MS   # Part 2 can be cut from the same video after a restart empties /tmp
            B.note(slot, MS.after_collect(acct, slot))
        except Exception as e:  # noqa: BLE001
            B.note(slot, f"master not kept: {e}"[:300])
    try:                              # THE VS CARD (vscard.py, 2026-10-28): a fight between two named people opens on
        from . import vscard as VS    # its fighting-game card; off unless the account's vs_card switch is on. Before
        VS.apply(cfg, acct, slot)     # after_take, so the pre-hook test sees it (one opener, never two)
    except Exception as e:  # noqa: BLE001  (a clip is never lost over its opener)
        B.note(slot, f"no VS card: {e}"[:300])
    try:                              # the social parts' first comment defaults, coach and learner inputs (social.py)
        from . import social as SOC
        SOC.after_take(cfg, acct, slot, folder)
    except (Exception, SystemExit) as e:  # noqa: BLE001  (a clip is never lost over its extras)
        try:
            from . import registry as REG
            REG._log_error("social:after_take", e)
        except Exception:  # noqa: BLE001
            pass
    try:                              # THE PRE-HOOK WHEN THE COLD OPEN FAILS (prehook.fallback, 2026-10-29): a clip that
        from . import prehook as PH   # opens on its headline alone gets one, after the test has had its turn; off
        PH.fallback(cfg, acct, slot)  # unless the account's pre_hook.fallback switch is on
    except Exception as e:  # noqa: BLE001  (a clip is never lost over its opener)
        B.note(slot, f"no fallback pre-hook: {e}"[:300])
    try:                              # THE COVER ART (coverart.py, 2026-10-30): a designed cover beside the clip, off
        from . import coverart as CA  # unless the account's cover_art switch is on; after the VS card (a fight keeps
        said = CA.apply(cfg, acct, slot)  # its card as its cover)
        if said:
            B.note(slot, said)
    except Exception as e:  # noqa: BLE001  (a clip is never lost over its cover)
        B.note(slot, f"no cover art: {e}"[:300])
    try:                              # THE OPENER RULE (opener.py, 2026-10-29): what the clip opens on, and why,
        from . import opener as OP    # for Clip Review and the coach
        if acct.maker == "politics":
            OP.record(acct, slot)
    except Exception as e:  # noqa: BLE001  (a label: never stops a collect)
        B.note(slot, f"no opener record: {e}"[:300])
    return (f"{label}: ready, {secs:.1f}s" + (f" ({cuts})" if cuts else "")
            + (f", CAST TO CONFIRM: {confirm}" if confirm else ""))


def _killed(acct: C.Account, slot: dict, folder: Path) -> Tuple[Optional[dict], str]:
    """(the kill list entry this delivery runs into, why), or (None, ''). An unreadable kill list or slate never
    stops a collect."""
    from . import reasons as R
    try:
        if not R.entries(acct):
            return None, ""
        return R.hit(acct, R.folder_identity(acct, folder, str(slot.get("publish_at") or "")[:10], slot))
    except Exception:  # noqa: BLE001
        return None, ""


def _refuse(slot: dict, mp4: Path, folder: Path, secs: float, why: str) -> None:
    """A clip outside the account's length band is never drafted, and since 2026-10-18 it no longer ends its slot's
    day (audit gta-yield): a 'refused' slot was out of reach of every maker, the 03:45 retry and both gap-fills,
    which fill only open or failed slots, until a person pressed Make again. The slot goes back to FAILED (fillable:
    the next pass, the retry or a gap-fill makes it again; review shows it as a problem with this note), and the
    clip is kept in the slot's refused_clips by its SHA-256, so collect never takes it, here or in any other slot."""
    slot.pop("source_folder", None)               # the refused folder is not this slot's source
    rec = {"sha256": B.sha256_file(mp4), "seconds": secs, "slug": folder.name.split(" ", 1)[-1],
           "from": str(folder), "why": why, "at": B.utcnow()}
    slot["refused_clips"] = ([r for r in slot.get("refused_clips") or [] if isinstance(r, dict)] + [rec])[-5:]
    slot.update(status=B.FAILED, fail_kind="refused", failed_at=_failed_at())
    B.note(slot, f"REFUSED {folder.name}: {why}; not drafted. The slot stays open for another clip (the next make, "
                 f"the 03:45 retry or a gap-fill); this clip is never taken")


def _failed_at() -> str:
    """When a slot failed, as makers.fail_stamp writes it (make_passes tells a pass's new failures by it)."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def refused_on(board: dict) -> Dict[str, str]:
    """sha256 -> the slot whose collect refused that clip (_refuse), for every slot of the board."""
    out: Dict[str, str] = {}
    for s in (board.get("slots") or {}).values():
        for r in s.get("refused_clips") or []:
            if isinstance(r, dict) and r.get("sha256"):
                out[str(r["sha256"])] = str(s.get("slot") or "")
    return out


def source_of(acct: C.Account, folder: Path, day: str = "") -> Dict[str, str]:
    """Who published the clip's source and its rights, for the Stats tab ({} when nothing says; then the slot keeps
    what the maker wrote, as the GTA maker writes host itself).

    A source.json beside the clip wins ({"host" or "publisher", "rights"}). Otherwise the Politics's job: named by the
    clip's cast-check.json, or found in the account's Publish/schedule.json by the clip's slug (that day's entry
    first), as igcut.find_job does; its Jobs/<job>/project.json has source_channel, source_url and rights."""
    def read(p: Path) -> dict:
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    own = read(folder / "source.json")
    if own.get("host") or own.get("publisher"):
        return {k: str(v) for k, v in (("host", own.get("host") or own.get("publisher")), ("rights", own.get("rights")))
                if v}
    m = SLOT_DIR.match(folder.name)
    slug = m.group(2) if m else ""
    job = str(read(folder / "cast-check.json").get("job") or "")
    if not job and slug:
        sched = read(acct.account_dir / "Publish" / "schedule.json")
        days = sorted(sched, reverse=True)
        for d in ([day] if day in sched else []) + days:
            slots = sched.get(d) if isinstance(sched.get(d), dict) else {}
            ent = next((e for e in slots.values() if isinstance(e, dict) and e.get("slug") == slug), None)
            if ent and ent.get("job"):
                job = str(ent["job"])
                break
    if not job:
        return {}
    pj = read(acct.account_dir / "Jobs" / job / "project.json")
    from urllib.parse import urlparse
    host = pj.get("source_channel") or urlparse(str(pj.get("source_url") or "")).netloc
    return {k: str(v) for k, v in (("host", host), ("rights", pj.get("rights"))) if v}


# the lane a GTA clip's template belongs to, as the board and Stats name it (the maker's own slot formats)
GTA_LANES = {"underlay": "underlay", "pip": "pip", "gameplay": "gameplay", "gameplay-pip": "gameplay",
             "gameplay-clean": "gameplay"}


def gta_provenance(acct: C.Account, folder: Path, day: str = "") -> Dict[str, str]:
    """GTA (audit GTA-14, 2026-10-17): the creator, source and lane of a delivered clip, read off its own skill job,
    so a slot filled by hand or re-made credits the clip it holds. The 10-17 editorial re-make moved clips between
    slots and kept the plan's host and source on 6 of 10 slots, so Stats and the scout's ground truth read the wrong
    creator and lane; and a source cut by hand never reached makers.used_sources (GTA-13).
    The job: the maker's pack beside the folder (<HHMM>.pack.json), else the account's Publish/schedule.json by the
    clip's slug (its own slot on that day first). Its project.json: the skill's work/<job>/, else the account's
    Jobs/<job>/_project/. {} when nothing says; then the slot keeps what the maker wrote."""
    def read(p: Path) -> dict:
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}

    m = SLOT_DIR.match(folder.name)
    if not m:
        return {}
    hhmm, slug = m.group(1), m.group(2)
    job = template = ""
    pack = read(folder.parent / f"{hhmm}.pack.json")
    if pack.get("slug") == slug and pack.get("job"):
        job, template = str(pack["job"]), str(pack.get("template") or "")
    if not job:
        sched = read(acct.account_dir / "Publish" / "schedule.json")
        hh = f"{hhmm[:2]}:{hhmm[2:]}"
        for d in ([day] if day in sched else []) + sorted((x for x in sched if x != day), reverse=True):
            slots = sched.get(d) if isinstance(sched.get(d), dict) else {}
            mine = slots.get(hh) if isinstance(slots.get(hh), dict) and slots[hh].get("slug") == slug else None
            ent = mine or next((e for e in slots.values() if isinstance(e, dict) and e.get("slug") == slug), None)
            if ent and ent.get("job"):
                job, template = str(ent["job"]), str(ent.get("template") or "")
                break
    if not job:
        return {}
    pj = read(acct.skill_dir / "work" / job / "project.json") or \
        read(acct.account_dir / "Jobs" / job / "_project" / "project.json")
    if not pj:
        return {}
    host = pj.get("host")
    host = (host.get("name") if isinstance(host, dict) else host) or pj.get("source_channel")
    out = {"job": job}
    for k, v in (("host", host), ("source_url", pj.get("source_url")), ("source_title", pj.get("source_title"))):
        if v:
            out[k] = str(v)
    lane = GTA_LANES.get(template.strip().lower()) or GTA_LANES.get(str(pj.get("_run_template") or "").strip().lower())
    if lane:
        out["format"] = lane
    return out


def set_segment(acct: C.Account, slot: dict) -> None:
    """The Politics: the slot's format becomes the delivered clip's segment (its slate `template`: room, gaggle...), so
    the board says what the clip is (audit LDG-11). Read through the skill's Publish/schedule.json; left as it is
    when that cannot be read. Other accounts: unchanged (GTA's slot format picks the lane)."""
    if acct.maker != "politics" or not slot.get("slug"):
        return
    try:
        sched = json.loads((acct.account_dir / "Publish" / "schedule.json").read_text())
    except (OSError, ValueError):
        return
    day = str(slot.get("publish_at") or "")[:10]
    for d in [day] + sorted((x for x in sched if x != day), reverse=True):
        ent = next((e for e in (sched.get(d) or {}).values() if (e or {}).get("slug") == slot["slug"]), None)
        if not ent or not ent.get("job"):
            continue
        try:
            slate = json.loads((acct.account_dir / "Jobs" / ent["job"] / "slate.json").read_text())
        except (OSError, ValueError):
            return
        clip = next((c for c in slate.get("clips") or [] if c.get("slug") == slot["slug"]), None)
        fmt = str((clip or {}).get("template") or "").strip().lower()
        if fmt and fmt != slot.get("format"):
            B.note(slot, f"format {slot.get('format')} -> {fmt} (the clip's segment)")
            slot["format"] = fmt
        return


def attach_cast_check(slot: dict, folder: Path, dest: Path) -> str:
    """The engine's cast-check.json beside a clip: people on it whom nobody has checked yet.

    The owner, 2026-10-17: "For a one-speaker clip whose title names the person, let the bot cut it the same night
    and I'll confirm the cast at the 05:30 sign-off." viral-clip-engine lets such a clip through on a provisional
    cast entry and its day folder writes cast-check.json; the card shows it, and Approve records the owner's check
    in the job's cast.json (signoff.confirm_cast). Not part of what is signed: the signature is the clip and words."""
    slot.pop("cast_check", None)
    src = folder / "cast-check.json"
    if not src.is_file():
        return ""
    try:
        cc = json.loads(src.read_text())
        people = [p for p in (cc.get("people") or []) if str(p.get("name") or "").strip()]
    except (OSError, ValueError, AttributeError):
        B.note(slot, "cast-check.json beside the clip could not be read; check the cast by eye")
        return "cast-check.json unreadable"
    if not people:
        return ""
    shutil.copy2(src, dest / src.name)
    slot["cast_check"] = {"people": people, "source_title": str(cc.get("source_title") or ""),
                          "cast_file": str(cc.get("cast_file") or ""), "job": str(cc.get("job") or "")}
    names = ", ".join(str(x["name"]) for x in people)
    bot = any(p.get("by_bot") and not p.get("named_as") for p in people)
    B.note(slot, f"CAST TO CONFIRM: {names} (" + ("not checked by a person yet" if bot else "named in the source's title")
           + "; approving is the check)")
    return names
