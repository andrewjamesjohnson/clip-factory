"""The sign-off. Nothing reaches an outlet unless it was signed: that exact clip, those exact words and that exact
time, by a person on the approvers list, or (autosign.py, the owner's call of 2026-10-19) by the factory itself
30 minutes before the slot, under its own name. The signature is a hash; if any of it changes afterwards the outlet
refuses.

Approve is the whole hand-over: it signs, then schedules (outlets.hand_off), and the poster takes it from there.
Pull takes a scheduled clip back before it goes out (it is ready to sign again); Decline and Make again are for a clip
nobody signed.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from . import board as B
from . import config as C


def _who(cfg: C.Config, by: str, acct: Optional[C.Account] = None) -> str:
    """The signer's name as the list spells it: the account's own "approvers" when it has them (a client account,
    accounts.py), else the global list. A client's name never signs the owner's clips, nor the owner's a client's
    unless he is on its list."""
    own = [str(x) for x in ((acct.raw.get("approvers") if acct is not None else None) or [])]
    names = {a.lower(): a for a in (own or cfg.approvers)}
    if by.lower() not in names:
        if own:
            raise SystemExit(f"{by!r} is not on {C.possessive(acct.name)} approvers list in {cfg.path} "
                             f"({', '.join(own)})")
        raise SystemExit(f"{by!r} is not on the approvers list in {cfg.path} ({', '.join(cfg.approvers) or 'empty'})")
    return names[by.lower()]


def _out(s: dict) -> List[str]:
    """The platforms this slot already went out on (posted, or uploaded to YouTube)."""
    return [p for p, r in (s.get("outlets") or {}).items() if r.get("post_id") or r.get("video_id")]


def _already_out(s: dict) -> str:
    """A slot signed again: what it already has out, and whether that is still the clip it shows. The 2026-10-17
    audit: after Pull and Approve, YouTube never got the new clip and the card said "uploaded"; the old one went
    public. So an upload of another clip is set aside (make it private, then Try again uploads this one). When it is
    not known which clip a platform has (a post from before 2026-10-18 that no Pull dated back), nothing is set aside
    or cleared, and the words say what to check."""
    from . import outlets as O
    from . import poster as P
    outs = s.get("outlets") or {}
    said = []
    for plat, r in sorted(outs.items()):
        old = r.get("old") or {}
        if old and not old.get("cleared") and not (r.get("post_id") or r.get("video_id")):
            # set aside before and never cleared with Try again (a Pull and Approve instead): Try again is still the
            # only way the clip as it is now goes up, after the old one is made private (2026-10-17 second review)
            r.update(gave_up=True, why=P.old_why(old))
            said.append(r["why"])
            continue
        if not (r.get("post_id") or r.get("video_id")):
            continue
        name = O.NAMES.get(plat, plat)
        same = P.same_clip(r, s, plat)
        if plat == "youtube_shorts" and same is False:
            outs[plat] = P.old_upload(r)
            B.note(s, f"{plat}: {outs[plat]['why']}")
            said.append(outs[plat]["why"])
        elif same is False:
            r["flag"] = f"{name} has the version from before: delete it there if it should not stay up"
            said.append(r["flag"])
        elif same is None and plat == "youtube_shorts":
            vid = r.get("video_id") or r.get("post_id")
            said.append(f"YouTube keeps the upload from before ({vid}). If you changed the clip, make that one "
                        f"private in Studio, https://studio.youtube.com/video/{vid}/edit")
        elif same is None:
            said.append(f"{name} keeps the post from before: if you changed the clip, delete it there")
        else:
            r.pop("flag", None)
            if plat == "youtube_shorts":
                said.append("YouTube keeps its upload from before (the same clip)")
    return "; ".join(said)


def _carry(r: dict) -> dict:
    """What a platform that is not out keeps when a clip is pulled or tried again: a send whose answer never came
    (it is looked up first, so it is never sent twice), and a set-aside OLD YouTube upload (so the stats read never
    ties that one back to the slot)."""
    return {k: r[k] for k in ("attempt", "old") if r.get(k)}


def _shas(s: dict) -> tuple:
    """The clip's, its cuts' and its words' hashes as the board has them: equal before and after _as_is means
    nothing was edited since it was collected."""
    return ((s.get("clip") or {}).get("sha256"),
            tuple(sorted((p, (c or {}).get("sha256")) for p, c in (s.get("cuts") or {}).items())),
            tuple(sorted((p, (c or {}).get("sha256")) for p, c in (s.get("copy") or {}).items())))


def _as_is(acct: C.Account, s: dict) -> List[str]:
    """Before a signature: take the clip and its words as they are on disk now, which is what the review page shows,
    so an edit made before signing is simply part of what is signed (only an edit AFTER signing voids it). A clip
    re-rendered since it was collected must still be inside the account's length band. Problems that stop it."""
    from . import collect as K
    clip = s.get("clip") or {}
    p = Path(clip.get("path", ""))
    if not p.is_file():
        return [f"the clip is gone: {p}"]
    sha = B.sha256_file(p)
    if sha != clip.get("sha256"):
        secs = K.probe_seconds(p)
        if secs is None:
            return [f"could not read the length of {p.name}"]
        if not acct.min_seconds - 0.05 <= secs <= acct.max_seconds + 0.05:
            return [f"{p.name} is {secs:.1f}s now, outside {C.possessive(acct.name)} "
                    f"{acct.min_seconds:.0f}-{acct.max_seconds:.0f}s"]
        clip.update(sha256=sha, seconds=secs)
        B.note(s, f"the clip changed on disk after it was collected ({secs:.1f}s now); signed as it is")
    problems, edited = [], []
    from . import outlets as O
    for plat, cut in sorted((s.get("cuts") or {}).items()):       # a platform's own cut, signed with the clip
        cp = Path(cut.get("path", ""))
        if not cp.is_file():
            problems.append(f"the {plat} cut is gone: {cp}")
            continue
        h = B.sha256_file(cp)
        if h != cut.get("sha256"):
            secs = K.probe_seconds(cp)
            lo, hi = O.band(acct, plat, cut=True)
            if secs is None or not lo - 0.05 <= secs <= hi + 0.05:
                problems.append(f"the {plat} cut is {secs}s now, outside {lo:.0f}-{hi:.0f}s")
                continue
            cut.update(sha256=h, seconds=secs)
            B.note(s, f"the {plat} cut changed on disk after it was collected ({secs:.1f}s now); signed as it is")
    for plat, c in sorted((s.get("copy") or {}).items()):
        cp = Path(c.get("path", ""))
        if not cp.is_file():
            problems.append(f"the {plat} words are gone: {cp}")
            continue
        h = B.sha256_text(cp.read_text())
        if h != c.get("sha256"):
            c["sha256"] = h
            edited.append(plat)
    if edited:
        B.note(s, f"words edited after collecting ({', '.join(edited)}); signed as they are now")
    if s.get("extras"):                        # its first comment, poll and trial flag (extras.py), signed with it
        try:
            from . import extras as EX
            for line in EX.adopt(s):
                B.note(s, line)
        except Exception as e:  # noqa: BLE001  (never sign extras that could not be checked)
            problems.append(f"the first comment or poll could not be checked ({type(e).__name__}: {str(e)[:120]})")
    return problems


def _verdict(cfg: C.Config, acct: C.Account, day: date, s: dict, verdict: str, who: str,
             reasons: Optional[List[str]] = None, note: str = "") -> None:
    """One row in the account's learn/verdicts.jsonl (verdicts.py): what he said about this clip, read by the
    learner. Never stops a sign-off, a decline or a remake."""
    try:
        from . import verdicts as VD
        VD.record(cfg, acct, day, s, verdict, who, reasons=reasons or (), note=note)
    except Exception as e:  # noqa: BLE001
        try:
            from . import registry as REG
            REG._log_error(f"verdicts:record {verdict}", e)
        except Exception:  # noqa: BLE001
            pass


# Where a cast file may be written from a sign-off: the engine's job folders, nothing else.
CAST_ROOTS = (Path.home() / "Desktop" / "Clipping" / "Jobs", Path.home() / "Desktop" / "Andrew Johnson" / "Clipping" / "Jobs")
# The words viral-clip-engine cast.BOT_VERIFIED reads: a `verified` naming Claude, Hermes or a bot, or saying
# "NEEDS ANDREW", was written by a bot and is no person's check (audit LDG-6; Andrew's call 6, 2026-10-17: those
# entries go back to the check at sign-off). The owner's own line below never uses these words.
BOT_VERIFIED = re.compile(r"(?<![-\w])claude(?![-\w])|(?<![-\w])hermes(?![-\w])|(?<![-\w])bots?(?![-\w])|needs\s+andrew",
                          re.I)
# A job's cast files: cast.json, and a proposal beside it (cast.prep-<x>.json; the cast queue, castqueue.py).
CAST_FILE = re.compile(r"^cast(?:\.prep-[A-Za-z0-9_-]+)?\.json$")


def cast_place_ok(path: Path, prep: bool = False) -> bool:
    """A cast file the factory may write: a job's cast.json (or, prep=True, a cast.prep-*.json beside it), inside
    CAST_ROOTS by its real path."""
    try:
        real = Path(path).expanduser().resolve()
        named = real.name == "cast.json" or (prep and bool(CAST_FILE.match(real.name)))
        return named and any(real.is_relative_to(Path(r).resolve()) for r in CAST_ROOTS)
    except (OSError, ValueError, AttributeError):
        return False


def edit_cast(job: Path, names: List[str], change):
    """The one way the factory writes a cast file (the sign-off's check, and the cast queue's taps). Each named file
    in the job folder must be a cast file inside CAST_ROOTS. Under one lock per job (state/castqueue/locks) it reads
    them, change({name: data}) edits them in place (or raises SystemExit: nothing is written), and each file that
    changed is written whole (a temp file, then a rename), in the order named (cast.json first). Returns change's
    answer."""
    import fcntl
    import hashlib
    import json
    import os
    paths = [Path(job) / n for n in names]
    for p in paths:
        if not cast_place_ok(p, prep=True) or not p.is_file():
            raise SystemExit(f"refused: {p} is not a job's cast file inside the Jobs folders")
    key = hashlib.sha1(str(Path(job).resolve()).encode()).hexdigest()[:16]
    lock = C.STATE / "castqueue" / "locks" / f"{key}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        datas, before = {}, {}
        for p in paths:
            try:
                datas[p.name] = json.loads(p.read_text())
            except (OSError, ValueError) as e:
                raise SystemExit(f"refused: {p.name} could not be read ({e})")
            if not isinstance(datas[p.name], dict):
                raise SystemExit(f"refused: {p.name} is not a cast file (no people)")
            before[p.name] = json.dumps(datas[p.name], sort_keys=True)
        said = change(datas)
        for p in paths:
            if json.dumps(datas[p.name], sort_keys=True) != before[p.name]:
                tmp = p.with_name(p.name + ".tmp")
                tmp.write_text(json.dumps(datas[p.name], indent=1) + "\n")
                os.replace(tmp, p)
    return said


def _said_not_them(s: dict, cfg: Optional[C.Config] = None) -> str:
    """Why a CAST TO CONFIRM clip is held by the owner's Not them in Clip Review's Casts, in one sentence ('' when
    it is not): such a clip is never signed (castqueue.holds: a Not them that stands, a person gone from cast.json,
    a plate he said Not them to that the cast renamed since, a Not them the tap log keeps)."""
    cc = s.get("cast_check") or {}
    if not cc.get("people"):
        return ""
    from . import castqueue as CQ
    return CQ.hold_note(cc, cfg)


def confirm_cast(cfg: C.Config, s: dict, who: str, day: date) -> str:
    """The owner's Approve on a CAST TO CONFIRM clip is the cast check (the owner, 2026-10-17: "I'll confirm the cast
    at the 05:30 sign-off"). Write it where viral-clip-engine reads it: `verified` on each named person in the job's
    cast.json, which a bot may never fill, so later clips from that job need no second check. Only a still-empty
    `verified`, or one a bot wrote (BOT_VERIFIED; the bot's words are kept as `verified_before`), is written, only
    into a cast.json inside the engine's Jobs folders. Returns the board note. A person the owner said Not them to
    in Clip Review's Casts is never written (castqueue.py). The write is edit_cast's: a lock, the whole file."""
    import json
    cc = s.get("cast_check") or {}
    names = ", ".join(str(p.get("name")) for p in cc.get("people") or [])
    path = Path(str(cc.get("cast_file") or "")).expanduser()
    ok_place = cast_place_ok(path)
    cc["confirmed"] = {"by": who, "at": B.utcnow(), "wrote": []}
    if not ok_place or not path.is_file():
        return f"cast confirmed by {who} for {names}, but {path} is not a job's cast.json: write `verified` by hand"
    try:
        json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return f"cast confirmed by {who} for {names}, but {path.name} could not be read ({e}): write `verified` by hand"
    from . import castqueue as CQ
    ids = {str(p.get("id") or "").lower() for p in cc.get("people") or []}
    stamp = cfg.now().strftime("%Y-%m-%d %H:%M")
    held: List[str] = []
    wrote_ids: List[str] = []

    def change(datas: dict) -> None:
        for p in datas["cast.json"].get("people") or []:
            was = str(p.get("verified") or "").strip()
            if str(p.get("id") or "").lower() not in ids:
                continue
            if CQ.rejected_now(p):            # he said Not them in Casts: an Approve is never his check of it
                held.append(str(p.get("name")))
                continue
            if not was or BOT_VERIFIED.search(was):
                pv = p.get("provisional") or {}
                how = ("over an unchecked note, kept as verified_before" if was else
                       f"provisional: named in the source's title as {pv.get('named_as')!r}")
                if was:
                    p["verified_before"] = was
                p["verified"] = (f"{who}, at the {day.isoformat()} sign-off in Clip Review ({stamp}): watched the "
                                 f"{s.get('slot')} clip {s.get('slug', '')} and approved it, confirming the cast "
                                 f"({how})")
                cc["confirmed"]["wrote"].append(str(p.get("name")))
                wrote_ids.append(str(p.get("id") or "").lower())
    try:
        edit_cast(path.parent, ["cast.json"], change)
    except SystemExit as e:
        return f"cast confirmed by {who} for {names}, but {e}: write `verified` by hand"
    for pid, n in zip(wrote_ids, cc["confirmed"]["wrote"]):
        try:
            CQ.log("approve-confirm", by=who, job=path.parent.name, file="cast.json", person=pid, name=n,
                   slot=f"{day.isoformat()} {s.get('slot')}")
        except OSError:
            pass
    held_note = f"; NOT written for {', '.join(held)}: you said not them in Casts" if held else ""
    if cc["confirmed"]["wrote"]:
        return (f"cast confirmed by {who}: {', '.join(cc['confirmed']['wrote'])} now verified in {path}"
                + held_note)
    return f"cast confirmed by {who} for {names} (already verified in {path.name})" + held_note


def approve(cfg: C.Config, acct: C.Account, day: date, slots: Optional[List[str]], by: str,
            now: Optional[datetime] = None, seen: Optional[Dict[str, str]] = None, auto: bool = False,
            everything: bool = False) -> List[str]:
    """Sign and schedule: the clips named (or every ready one) go out at their slots by themselves.

    `seen` (from the review page: slot -> the card's B.seen_key) signs a clip only if it is still exactly what the
    page showed; the command line passes nothing and signs what is on disk.

    `auto` (autosign.run only): the factory signs under its own name (autosign.BY), re-checks autosign.why_not
    inside the board's lock, never writes `verified` into a cast, and notes a refusal once (it is not tried again
    until the clip or its words change).

    One moment, one slot a day (moments.py, 2026-10-22): a second slot holding the same clip (its slug, its delivery
    or the same seconds of its source) is never signed, and the line names the first. Approve all (`slots` None,
    or `everything`: the review page's button) and the automatic sign-off also leave alone a clip the night marked
    stale and a pair of unsigned twins: only a one-by-one Approve signs those."""
    from . import autosign as A
    from . import moments as MO
    from . import outlets as O
    from . import poster as P
    who = A.BY if auto else _who(cfg, by, acct)
    now = now or cfg.now()
    lines: List[str] = []
    signed: List[str] = []
    extra: Dict[str, str] = {}
    relift: List[tuple] = []
    bulk = auto or everything or slots is None
    with B.edit(cfg, acct, day, create=False) as board:
        keys = [C.normal_slot(s) for s in slots] if slots else sorted(board["slots"])
        pairs = MO.twins(cfg, acct, day, board)          # the slots holding one moment twice (moments.py)
        for k in keys:
            s = board["slots"].get(k)
            if s is None:
                lines.append(f"  {k}: not a slot on this board")
                continue
            if s["status"] not in B.SIGNABLE:
                if slots:  # asked for by name: say why not
                    lines.append(f"  {k}: {s['status']}, nothing to sign")
                continue
            if auto:
                why = A.why_not(cfg, acct, s, now)
                if why:
                    lines.append(f"  {k}: not signed automatically: {why}")
                    continue
            second = MO.refuse_second(board, pairs.get(k, []))
            if second:                         # the same moment went first in another slot: this one never goes
                lines.append(f"  {k}: NOT signed: {second}")
                if auto:
                    B.note_once(s, f"not signed automatically: {second}")
                continue
            skip = MO.bulk_skip(board, k, pairs.get(k, [])) if bulk else ""
            if skip:                           # stale, or one of two unsigned twins: a person approves it alone
                lines.append(f"  {k}: not signed {'automatically' if auto else 'with Approve all'}: {skip}")
                continue
            if seen is not None and seen.get(k) != B.seen_key(s):
                lines.append(f"  {k}: NOT signed, it changed since the page showed it (or was not on it yet): "
                             "look at it again")
                continue
            if P.standin(s):
                lines.append(f"  {k}: NOT signed, it is a stand-in test clip (never for a real account)")
                continue
            held = _said_not_them(s, cfg)
            if held:
                lines.append(f"  {k}: NOT signed: {held}")
                continue
            before = _shas(s)
            problems = _as_is(acct, s)
            if problems:
                lines.append(f"  {k}: NOT signed, {'; '.join(problems)}")
                if auto:                       # noted once; tried again only once the clip or its words change
                    B.note_once(s, f"not signed automatically: {'; '.join(problems)}")
                    s["auto_refused"] = B.seen_key(s)
                continue
            from . import reasons as R
            gate = None if auto else R.held_by(cfg, acct, s, now)   # a check the automatic sign-off held it for
            s["signoff"] = {"by": who, "at": B.utcnow(), "hash": B.content_hash(s)}
            if auto:
                s["signoff"]["auto"] = True
            if gate:                           # the Stats tab's needless holds: a person approved what a check held
                gate.update(by=who, at=s["signoff"]["at"], clip=(s.get("clip") or {}).get("sha256"),
                            unchanged=_shas(s) == before)
                s["gate_holds"] = ([h for h in s.get("gate_holds") or [] if isinstance(h, dict)] + [gate])[-10:]
            if not auto:
                relift.append((k, (s.get("clip") or {}).get("sha256")))
                MO.approved_anyway(s, who)    # a clip marked stale, approved on its own: the notes say whose call
            s["status"] = B.APPROVED
            s.pop("handed", None)              # handed out again at this signature's hand-off
            A.clear(s)
            if auto:
                B.note(s, f"signed automatically by {who}: nobody pressed Approve before its time")
            else:
                B.note(s, f"approved by {who}")
            # the verdict row carries the signer: the factory's own name on an automatic sign-off, never a person's
            _verdict(cfg, acct, day, s, "approve", who,
                     note="signed automatically: nobody pressed Approve before its time" if auto else "")
            if (s.get("cast_check") or {}).get("people"):
                if auto:                       # nobody watched it: no person's cast check is written
                    left = A.unchecked(cfg, s)
                    if left:
                        B.note(s, f"cast not checked by a person ({', '.join(left)}): signed automatically, "
                                  f"`verified` left as it was")
                else:
                    B.note(s, confirm_cast(cfg, s, who, day))
            said = _already_out(s)
            if said:
                extra[k] = said
            signed.append(k)
    if not signed:
        return lines or ["  nothing waiting for a sign-off"]
    from . import reasons as R
    for k, sha in relift:                  # approved again after a coded Pull: it goes out, so its kill entry lifts
        try:
            for i in R.lift_slot(acct, day, k, sha, who):
                extra[k] = (extra.get(k, "") + "; " if extra.get(k) else "") + f"kill list entry {i} lifted"
        except OSError:
            pass
    O.hand_off(cfg, acct, day)
    bd = B.read(acct, day) or {"slots": {}}
    for k in signed:
        s = bd["slots"].get(k, {})
        if s.get("status") == B.SCHEDULED:
            lines.append(f"  {k} {s.get('slug', '')}: {'signed automatically' if auto else 'approved'} by {who}, "
                         f"goes out {P.goes_out(s, now)}"
                         + (f". {extra[k]}" if extra.get(k) else ""))
        else:
            lines.append(f"  {k} {s.get('slug', '')}: {'signed automatically' if auto else 'approved'} by {who} but "
                         f"NOT scheduled: {B.last_note(s)}")
    return lines


def pull(cfg: C.Config, acct: C.Account, day: date, slots: List[str], by: str, code: Optional[str] = None,
         reason: str = "") -> List[str]:
    """Take signed clips back before they go out: unsigned, ready again, nothing more posts. What already went out
    stays out (the factory cannot delete a post), and the line says where.

    `code` (reasons.CODES; words with no code count as Other) says why: it goes on the slot, and the clip on the
    account's kill list (reasons.py). No code and no words: a Pull to look again (a platform's cut), nothing recorded."""
    from . import outlets as O
    from . import poster as P
    from . import reasons as R
    who = _who(cfg, by, acct)
    why = _reason_code(code, reason)
    lines: List[str] = []
    pulled = 0
    with B.edit(cfg, acct, day, create=False) as board:
        for k in (C.normal_slot(s) for s in slots):
            s = board["slots"].get(k)
            if s is None:
                lines.append(f"  {k}: not a slot on this board")
                continue
            if s["status"] not in B.SIGNED:
                lines.append(f"  {k}: {s['status']}, nothing to pull")
                continue
            out = _out(s)
            P.backfill_shas(s)                 # while it is still as signed: which clip each platform got
            r = R.record(s, "pull", why, reason, who) if why else None
            if r is not None:                  # the learner's log (verdicts.py) has the Pull and its reason too
                _verdict(cfg, acct, day, s, "pull", who, [why.key], reason)
            s.pop("signoff", None)
            s.pop("handed", None)
            s["status"] = B.READY
            from . import autosign as A        # a pulled clip never signs itself again: a person approves it
            A.hold(s, who)
            # what went out stays on record; a platform that gave up or was waiting starts fresh if it is signed again,
            # except a send whose answer never came: it is looked up first, so it is never sent twice
            keep = {}
            for p, r in (s.get("outlets") or {}).items():
                P.undo_old_rematch(r)          # the OLD clip the stats read tied back is not a post of this one
                if r.get("post_id") or r.get("video_id"):
                    keep[p] = r
                elif _carry(r):
                    keep[p] = _carry(r)
            s["outlets"] = keep
            from . import collect as K
            newer = K.take_newer(cfg, acct, day, s)   # a newer render that waited beside the signed clip (moments.py)
            if newer:
                lines.append(f"  {k}: {newer}; watch it, then approve")
            if s.get("source_folder") and Path(s["source_folder"]).is_dir():
                # a platform's cut that waited for the signature joins it now (after a newer render: nothing new)
                took = K.attach_cuts(cfg, acct, s, Path(s["source_folder"]))
                if took:
                    lines.append(f"  {k}: {took}; look at it, then approve")
            B.note(s, f"pulled by {who}" + (f": {R.words(why, reason)}" if why else "")
                   + (f"; already out on {', '.join(out)}" if out else ""))
            pulled += 1
            line = f"  {k}: pulled, nothing more goes out"
            if r is not None:
                said = R.kill(cfg, acct, day, s, r)
                r["kill"] = said
                line += f" ({R.words(why, reason)}; {said})"
            from . import autosign as A
            if A.on(cfg, acct):
                line += " (it will not sign itself again: Approve sends it)"
            yt = (s.get("outlets") or {}).get("youtube_shorts") or {}
            old = yt.get("old") or {}
            if old.get("video_id") and not yt.get("video_id") and not old.get("cleared"):
                studio = f"https://studio.youtube.com/video/{old['video_id']}/edit"
                public = P.went_public({"video_id": old["video_id"], "at": old.get("at")},
                                       {"publish_at": old.get("publish_at") or s["publish_at"]})
                if public and public <= cfg.now():
                    line += (f". YouTube still has the OLD clip ({old['video_id']}), public since "
                             f"{P._hhmm(cfg, public.isoformat())}: make it private in Studio: {studio}")
                else:
                    line += (f". YouTube still has the OLD clip ({old['video_id']}): it goes public at "
                             f"{P._hhmm(cfg, public.isoformat() if public else s['publish_at'])} unless you make it "
                             f"private in Studio: {studio}")
            if yt.get("video_id"):
                when = yt.get("publish_at") or s["publish_at"]
                studio = f"https://studio.youtube.com/video/{yt['video_id']}/edit"
                public = P.went_public(yt, s)
                if public and public <= cfg.now():
                    line += (f". YouTube already has it, public since {P._hhmm(cfg, public.isoformat())}. To take it "
                             f"down, make it private in Studio: {studio}")
                else:
                    line += (f". YouTube already has it and makes it public at {P._hhmm(cfg, when)}. To stop that, make "
                             f"it private in Studio: {studio}. To keep it, approve the same clip again")
            others = [O.NAMES.get(p, p) for p in out if p != "youtube_shorts"]
            if others:
                line += f". Already posted on {', '.join(others)}: delete it there if it should not stay up"
            fly = P.in_flight(acct, day, k)
            if fly:
                line += f". {O.NAMES.get(fly, fly)} was sending it right then: if it lands anyway, the card says so"
            lines.append(line)
    if pulled:
        O.hand_off(cfg, acct, day)      # the day's export without it
    return lines


# the clip-fixer task's name (prompts/fixer.md, 2026-10-22): it may Try again a signed clip, never sign one, so it is
# accepted here only and is never on the approvers list
FIXER = "Clip Factory (fixer)"


def retry(cfg: C.Config, acct: C.Account, day: date, slots: List[str], by: str) -> List[str]:
    """Try again the platforms that gave up on a signed clip. The signature stands (nothing about the clip changed);
    a clip changed after signing cannot be retried: Pull it, look at it, and approve it again."""
    from . import outlets as O
    from . import poster as P
    who = FIXER if by == FIXER else _who(cfg, by, acct)
    lines: List[str] = []
    with B.edit(cfg, acct, day, create=False) as board:
        for k in (C.normal_slot(s) for s in slots):
            s = board["slots"].get(k)
            if s is None or s["status"] not in B.SIGNED:
                lines.append(f"  {k}: not scheduled, nothing to try again")
                continue
            problems = B.verify(s)
            if problems:
                lines.append(f"  {k}: NOT tried again, it changed after signing ({'; '.join(problems)}). "
                             "Pull it, look at it, and approve it again")
                continue
            outs = s.get("outlets") or {}
            for r in outs.values():            # the stats read tied a set-aside OLD upload back: that is not a post
                if isinstance(r, dict):
                    P.undo_old_rematch(r)
            gave = [p for p, r in outs.items() if r.get("gave_up") and not (r.get("post_id") or r.get("video_id"))]
            # a platform that takes this clip only as its own cut, and the slot has none: trying again would send the
            # whole clip and be refused at once (2026-10-17: Instagram gave up on the 76s clip, its cut waited in the
            # delivery folder, Try again re-sent the 76s clip). Its cut goes out through Pull, a look, and Approve
            uncut = {p: O.needs_cut(acct, s, p) for p in gave if O.needs_cut(acct, s, p)}
            # a cut outside its outlet's band as it is now (the 10-21 Instagram 50 -> 40s change): sending it again is
            # refused again. It needs a new cut (bandcheck.py), then Pull and Approve
            from . import bandcheck as BC
            off = {p: m for p, m in BC.slot_misses(acct, s).items() if p in gave and p not in uncut}
            for p, m in off.items():
                name = O.NAMES.get(p, p)
                if _cut_waits(acct, s, p):
                    lines.append(f"  {k}: {name}'s new cut is ready: Pull, watch it, then Approve (Try again would "
                                 f"send the old cut, and {name} cannot take it)")
                else:
                    lines.append(f"  {k}: {name} not tried again: {BC.words(m, acct)} "
                                 f"({BC.how(dict(m, account=acct.slug, date=day.isoformat()))})")
            again = [p for p in gave if p not in uncut and p not in off]
            for p, why in uncut.items():
                name = O.NAMES.get(p, p)
                if _cut_waits(acct, s, p):
                    lines.append(f"  {k}: {name}'s own cut is ready: Pull, watch it, then Approve "
                                 f"(Try again would send the whole clip, and {name} cannot take it)")
                else:
                    lines.append(f"  {k}: {name} not tried again, it needs its own cut ({why}): make the cut, "
                                 f"then Pull and Approve")
            if not again:
                if not uncut and not off:
                    lines.append(f"  {k}: nothing gave up")
                continue
            fresh = [p for p in again if outs[p].get("lookup_failed")]
            for p in again:
                r = s["outlets"].pop(p)
                keep = _carry(r)                   # a send with no answer is looked up first, never sent twice,
                if r.get("lookup_failed"):         # unless that look-up kept failing: the card asked to check first
                    keep.pop("attempt", None)
                if keep.get("old"):                # the card asked to make the old one private first: that is done
                    keep["old"] = dict(keep["old"], cleared=f"{who} {B.utcnow()}")
                if keep:
                    s["outlets"][p] = keep
            B.note(s, f"tried again by {who}: {', '.join(again)}"
                   + (f" ({', '.join(fresh)} sent fresh)" if fresh else ""))
            lines.append(f"  {k}: {O._names(again)} will be tried again"
                         + (f"; {O._names(fresh)}: sent fresh, the last try could not be checked" if fresh else ""))
    return lines


def _cut_waits(acct: C.Account, s: dict, plat: str) -> bool:
    """Is this platform's own cut in the clip's delivery folder, inside its band, and not on the slot yet (made after
    the clip was signed)? Pull takes it (collect.attach_cuts); a cut outside the band would be left out, so it is not
    'ready'. A cut on the slot that no longer fits its band (bandcheck.py) gives way to a new one in the folder."""
    from . import bandcheck as BC
    have = (s.get("cuts") or {}).get(plat)
    if (have and plat not in BC.slot_misses(acct, s)) or not s.get("source_folder") \
            or not Path(s["source_folder"]).is_dir():
        return False
    from . import collect as K
    from . import outlets as O
    src = K.cuts_in(Path(s["source_folder"])).get(plat)
    if src is None or (have and B.sha256_file(src) == have.get("sha256")):
        return False
    secs = K.probe_seconds(src)
    lo, hi = O.band(acct, plat, cut=True)
    return secs is not None and lo - 0.05 <= secs <= hi + 0.05


def _reason_code(code: Optional[str], reason: str):
    """The reasons.Code for a click or a command: the code named, else Other when there are words, else None."""
    from . import reasons as R
    try:
        c = R.code(code)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    return c or (R.BY_KEY["other"] if (reason or "").strip() else None)


def decline(cfg: C.Config, acct: C.Account, day: date, slots: List[str], by: str, reason: str = "",
            remake: bool = False, reasons: Optional[List[str]] = None, code: Optional[str] = None) -> List[str]:
    """Decline a clip nobody signed, or send it back to be made again (remake).

    Why, in the one reason list (reasons.CODES; verdicts.REASONS reads it): `code`, one tap on the review page's
    Decline or `--code`, or `reasons`, the codes of Make again's chips or `--reasons` (the first one is the slot's
    reason). Words with no code count as Other on the slot (the verdict keeps only codes he picked). The reason goes
    on the slot (reasons.record) and in the note ("HOOK (slow open)" for one tapped code; "too dark (reasons: hook,
    framing)" for chips, as the social layer wrote them), and with the verdict (verdicts.py), which is written before
    Make again takes the clip off the slot (the learner needs to know which clip it was). Every Decline puts the clip on the account's kill list (reasons.py: the night and the bench
    never cut that span or its killed b-roll again); Make again does not: it asks for another clip, not never this."""
    from . import reasons as R
    who = _who(cfg, by, acct)
    picked: List[str] = []
    if reasons:
        from . import verdicts as VD
        picked = VD.check(list(reasons))
    why = _reason_code(code or (picked[0] if picked else None), reason if not picked else "")
    if code and why is not None and why.key == "other" and not (reason or "").strip():
        raise SystemExit("Other needs words: say what was wrong (--reason)")
    chips = bool(picked)
    if code and why is not None and why.key not in picked:
        picked = [why.key] + picked            # the verdict: the codes he picked (words alone log no code)
    if code:                                   # one tap (Decline's panel) or --code
        said = R.words(why, reason)
        tail = f": {said}" if said else ""
    elif chips:                                # Make again's chips or --reasons: the social layer's words, as before
        from . import verdicts as VD
        said = R.words_many(picked, reason)
        tail = (f": {reason}" if reason else "") + f" (reasons: {VD.words(picked)})"
    else:                                      # words alone, or nothing
        said = (reason or "").strip()
        tail = f": {said}" if said else ""
    lines = []
    with B.edit(cfg, acct, day, create=False) as board:
        for k in (C.normal_slot(s) for s in slots):
            s = board["slots"].get(k)
            if s is None:
                lines.append(f"  {k}: not a slot on this board")
                continue
            if s["status"] == B.SCHEDULED:
                lines.append(f"  {k}: scheduled; pull it first (factory.py pull, or Pull on the review page)")
                continue
            if remake and _out(s):
                # a new clip in this slot would never reach the platforms this one already went out on
                lines.append(f"  {k}: already went out on {', '.join(_out(s))}; decline it instead of making it again")
                continue
            _verdict(cfg, acct, day, s, "remake" if remake else "decline", who, picked, reason)
            s.pop("signoff", None)
            r = R.record(s, "remake" if remake else "decline", why, reason, who, more=picked[1:]) \
                if (why or not remake) else None
            if remake:
                s["status"] = B.OPEN
                for k2 in ("source_folder", "clip", "copy", "folder", "slug", "cover", "outlets", "auto_hold",
                           "auto_refused"):
                    s.pop(k2, None)
                _clear_extras(s)
                B.note(s, f"sent back to be made again by {who}{tail}")
                lines.append(f"  {k}: open again, the bot will make a new clip")
            else:
                s["status"] = B.DECLINED
                B.note(s, f"declined by {who}{tail}")
                kl = R.kill(cfg, acct, day, s, r)
                r["kill"] = kl
                lines.append(f"  {k}: declined")      # the line review and the client page read as done
                lines.append(f"  {k}: {said + '; ' if said else ''}{kl}")
    return lines


def _clear_extras(s: dict) -> None:
    """Make again: the clip's first comment and poll go with it (extras.clear)."""
    if not s.get("extras"):
        return None
    try:
        from . import extras as EX
        EX.clear(s)
    except Exception:  # noqa: BLE001  (the pointer still goes: a new clip never inherits the old one's extras)
        s.pop("extras", None)
    return None
