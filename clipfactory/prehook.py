"""The pre-hook (Andrew, 2026-10-27): a 2-5 s unrelated viral moment that FINISHES its idea (the ball hits the lens,
the rock goes through the ice) plays before the clip, which then opens on its own punch line (the engine's cold
open) - "a double hook". It is arm B of a "pre_hook" experiment (learn.py), so it is put on at collect, after the
engine's preflight and grade, which would fail any clip whose first word comes after 0.8s (grade HOOK).

    library   ~/Desktop/CLIP FACTORY/Pre-hooks/library/library.json, cut by tools/make_prehooks.py there
    state     state/<account>/learn/prehooks.json  {"used": {name: iso}} for the rotation

What it does to a slot (arm B): picks the library clip used longest ago that fits under EVERY ceiling the slot's files
have (the clip under the account's max_seconds, each platform cut under its own cut band: the Politics's Instagram
40s, its YouTube cut 59s), and puts it in front of each file. The clip's own picture is never re-encoded: the
pre-hook is encoded to the file's exact stream header (its pixel shape and colour tags, the engine's x264
settings) and joined by stream copy; the sound is joined sample-exact. If a header still differs the file is
joined by re-encoding, and it says so. The file as delivered is kept in the draft folder's .no-prehook/. The
slot's sha256/seconds follow, and slot["pre_hook"] records what went on. The .srt beside the clip is left on the
clip's own clock on purpose: the coach and the learner read the clip's hook from it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C
from . import store

LIBRARY = Path.home() / "Desktop" / "CLIP FACTORY" / "Pre-hooks" / "library"
KEEP = ".no-prehook"                 # the delivered files, beside the drafts
FADE = 0.012                         # the pre-hook's sound fades at each end (the clip's own start is untouched)

# the engine's encoder (viral-clip-engine clipkit.render: the broadcast look's crf 17)
X264 = ["-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-x264-params", "keyint=50:min-keyint=25", "-r", "30"]
AAC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def state_path(acct) -> Path:
    return store.path(acct.slug, "learn", "prehooks.json")


def library(root: Optional[Path] = None) -> List[dict]:
    """The library's clips that are on disk, each with its full path: [{num, name, file, seconds, path, ...}]."""
    root = Path(root or LIBRARY)
    try:
        rows = json.loads((root / "library.json").read_text())
    except (OSError, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        p = root / str(r.get("file") or "")
        if isinstance(r, dict) and p.is_file() and isinstance(r.get("seconds"), (int, float)):
            out.append(dict(r, path=str(p)))
    return out


def ceilings(acct, slot: dict) -> List[Tuple[str, Path, float, float]]:
    """Each file the slot posts, with its length now and its ceiling: ("clip", path, secs, max) and one per cut."""
    from . import outlets as O
    out = []
    clip = slot.get("clip") or {}
    if clip.get("path"):
        out.append(("clip", Path(clip["path"]), _delivered_secs(clip), float(acct.max_seconds)))
    for plat, cut in sorted((slot.get("cuts") or {}).items()):
        if (cut or {}).get("path"):
            out.append((plat, Path(cut["path"]), _delivered_secs(cut), O.band(acct, plat, cut=True)[1]))
    return out


def _delivered_secs(row: dict) -> float:
    """A file's length as delivered (a second pass measures room from it, never from a joined file)."""
    return float(row.get("delivered_seconds") or row.get("seconds") or 0)


def pick(acct, files: List[Tuple[str, Path, float, float]], lib: Optional[List[dict]] = None) -> Optional[dict]:
    """The clip used longest ago (never used first, then library order) that fits under every file's ceiling."""
    lib = library() if lib is None else lib
    used = (store.read(state_path(acct), {}) or {}).get("used") or {}
    room = min((hi - secs for _, _, secs, hi in files), default=0.0)
    fits = [r for r in lib if float(r["seconds"]) <= room - 0.1]
    if not fits:
        return None
    order = {r["name"]: i for i, r in enumerate(lib)}
    return min(fits, key=lambda r: (str(used.get(r["name"]) or ""), order[r["name"]]))


def _probe(p: Path, entries: str) -> Dict[str, str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", f"stream={entries}",
                          "-of", "default=nw=1", str(p)], capture_output=True, text=True, check=False)
    return dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)


def _extradata(p: Path) -> str:
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_data", "-show_entries",
                           "stream=extradata,width,height,r_frame_rate,pix_fmt,profile", "-of", "default=nw=1",
                           str(p)], capture_output=True, text=True, check=False).stdout


def _video_seconds(p: Path) -> Optional[float]:
    """The picture's own length, whole frames (the file's length runs on into the AAC padding by a few ms, and a join
    there would put the clip off its frame grid)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets,r_frame_rate", "-of", "default=nw=1", str(p)],
                       capture_output=True, text=True, check=False)
    d = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    try:
        num, den = (float(x) for x in d["r_frame_rate"].split("/"))
        return int(d["nb_read_packets"]) * den / num
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def _seconds(p: Path) -> Optional[float]:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, check=False)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def _run(cmd: List[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-400:]}")


def join(pre: Path, target: Path) -> str:
    """Put `pre` in front of `target` in place. Returns how: "copy" (the target's picture untouched) or "re-encode"."""
    s = _probe(target, "width,height,sample_aspect_ratio,color_range,color_space,color_transfer,color_primaries")
    w, h = s.get("width", "1080"), s.get("height", "1920")
    sar = s.get("sample_aspect_ratio", "1:1")
    sar = "1/1" if sar in ("", "0:1", "N/A") else sar.replace(":", "/")
    tags = []
    for opt, key in (("-color_range", "color_range"), ("-colorspace", "color_space"),
                     ("-color_trc", "color_transfer"), ("-color_primaries", "color_primaries")):
        if s.get(key) and s[key] not in ("unknown", "N/A"):
            tags += [opt, s[key]]
    with tempfile.TemporaryDirectory() as td:
        enc = Path(td) / "pre.mp4"
        d = _seconds(pre) or 0.0
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(pre), "-vf",
              f"scale={w}:{h},setsar=sar={sar}:max=1000000", *tags, *X264,
              "-af", f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, d - FADE):.4f}:d={FADE}", *AAC,
              "-movflags", "+faststart", str(enc)])
        d = _video_seconds(enc) or d             # the sound is padded or cut to the picture: the clip stays in sync
        joined = Path(td) / "joined.mp4"
        if _extradata(enc) == _extradata(target):
            lst = Path(td) / "list.txt"
            lst.write_text(f"file '{enc}'\nduration {d:.6f}\nfile '{target.resolve()}'\n")
            _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(enc),
                  "-i", str(target), "-filter_complex",
                  f"[1:a]apad,atrim=end={d:.6f},asetpts=PTS-STARTPTS[p];[p][2:a]concat=n=2:v=0:a=1[a]",
                  "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", *AAC, "-movflags", "+faststart", str(joined)])
            how = "copy"
        else:
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(enc), "-i", str(target), "-filter_complex",
                  "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", *X264, *AAC,
                  "-movflags", "+faststart", str(joined)])
            how = "re-encode"
        shutil.move(str(joined), str(target))
    return how


def _keep(p: Path) -> None:
    """The file as delivered, once (a second pre-hook never goes on top of the first)."""
    keep = p.parent / KEEP / p.name
    if not keep.exists():
        keep.parent.mkdir(exist_ok=True)
        shutil.copy2(p, keep)


def _restore(p: Path) -> bool:
    keep = p.parent / KEEP / p.name
    if keep.is_file():
        shutil.copy2(keep, p)
        return True
    return False


def apply(cfg: C.Config, acct, slot: dict, lib: Optional[List[dict]] = None) -> Tuple[Optional[dict], str]:
    """Arm B on a slot just collected: (record, "") when the pre-hook went on, (None, why) when it could not."""
    files = ceilings(acct, slot)
    if not files:
        return None, "the slot holds no clip"
    lib = library() if lib is None else lib
    if not lib:
        return None, "the pre-hook library is empty or missing"
    choice = pick(acct, files, lib)
    if choice is None:
        room = min(hi - secs for _, _, secs, hi in files)
        return None, f"no pre-hook fits: {room:.1f}s of room under the tightest ceiling"
    pre, d = Path(choice["path"]), float(choice["seconds"])
    done, hows = [], []
    try:
        for kind, p, _secs, _hi in files:
            _keep(p)
            _restore(p)                  # always from the delivered file
            hows.append(join(pre, p))
            done.append((kind, p))
    except Exception as e:  # noqa: BLE001  (a clip is never lost over its pre-hook: every file goes back)
        for _kind, p in done:
            _restore(p)
        return None, f"the pre-hook could not be joined ({e})"[:300]
    for kind, p in done:
        secs = _seconds(p)
        row = slot["clip"] if kind == "clip" else slot["cuts"][kind]
        row.setdefault("delivered_sha256", row.get("sha256"))         # its identity on the board (B.clip_idents)
        row.setdefault("delivered_seconds", row.get("seconds"))
        row.update(sha256=B.sha256_file(p), seconds=round(secs, 3) if secs else row.get("seconds"))
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rec = {"name": choice["name"], "num": choice.get("num"), "seconds": d, "source": choice.get("source"),
           "files": [k for k, _ in done], "join": sorted(set(hows)), "at": now}
    slot["pre_hook"] = rec
    with store.edit(state_path(acct), dict) as st:
        st.setdefault("used", {})[choice["name"]] = now
    B.note(slot, f"pre-hook {choice['name']} ({d:.1f}s) put in front of "
                 f"{', '.join(k for k, _ in done)}" + ("; joined by re-encoding" if "re-encode" in hows else ""))
    return rec, ""


def apply_cut(acct, slot: dict, plat: str, lib: Optional[List[dict]] = None) -> str:
    """A platform cut that arrived after its clip took arm B gets the same pre-hook when it fits ('' when it went
    on, else why not: the cut then posts as delivered)."""
    rec = slot.get("pre_hook") or {}
    cut = (slot.get("cuts") or {}).get(plat) or {}
    if not rec or not cut.get("path"):
        return "no pre-hook on this slot"
    from . import outlets as O
    hit = next((r for r in (library() if lib is None else lib) if r["name"] == rec.get("name")), None)
    if hit is None:
        why = f"the pre-hook {rec.get('name')} is no longer in the library"
    elif _delivered_secs(cut) + float(hit["seconds"]) > O.band(acct, plat, cut=True)[1] - 0.1:
        why = f"{rec.get('name')} would take the {plat} cut past {O.band(acct, plat, cut=True)[1]:.0f}s"
    else:
        p = Path(cut["path"])
        try:
            _keep(p)
            _restore(p)
            join(Path(hit["path"]), p)
        except Exception as e:  # noqa: BLE001
            _restore(p)
            why = f"the pre-hook could not be joined ({e})"[:300]
        else:
            secs = _seconds(p)
            cut.setdefault("delivered_sha256", cut.get("sha256"))
            cut.setdefault("delivered_seconds", cut.get("seconds"))
            cut.update(sha256=B.sha256_file(p), seconds=round(secs, 3) if secs else cut.get("seconds"))
            rec.setdefault("files", []).append(plat)
            B.note(slot, f"pre-hook {rec.get('name')} put in front of the late {plat} cut")
            return ""
    B.note(slot, f"no pre-hook on the {plat} cut: {why}")
    rec.setdefault("skipped", {})[plat] = why
    return why


# ------------------------------------------------------------------------------------------------------ the fallback
# THE PRE-HOOK WHEN THE COLD OPEN FAILS (Andrew, 2026-10-28: 28 of 30 Politics clips opened on the headline alone; his
# OK that night: "pre-hook when it fails"). A clip whose cold open the engine refused, with no VS card and no pre-hook
# from the test, opens on nothing but its headline: it gets the pre-hook at collect, after the test has had its turn.
# Off unless the account's pre_hook.fallback switch is on, and only on boards from pre_hook.fallback_from. Such a clip
# sits out of the pre-hook test, in both arms (learn._cannot_apply): the test then compares only clips that open on
# their own line. Its record is slot["pre_hook"] with "fallback": the engine's reason, so a late cut gets the same one.
def cold_record(acct, slot: dict) -> Tuple[Optional[dict], str]:
    """The engine's cold open record for the slot's clip (clips/<stem>.coldopen.json in its job), or (None, why not).
    Only when the job's clip is the file delivered: a record of another render says nothing about this one."""
    from . import vscard as VS
    clip = slot.get("clip") or {}
    if not clip.get("path"):
        return None, "the slot holds no clip"
    job = VS.slot_job(slot, acct)
    if not job:
        return None, "the clip's job is not known"
    stem = Path(clip["path"]).stem
    made = VS.JOBS / job / "clips" / f"{stem}.mp4"
    rec = made.with_suffix(".coldopen.json")
    if not rec.is_file():
        return None, f"no cold open record for {stem} in {job} (the cold open is off there, or an older render)"
    if not made.is_file() or B.sha256_file(made) != (clip.get("delivered_sha256") or clip.get("sha256")):
        return None, f"the cold open record in {job} is another render's"
    try:
        got = json.loads(rec.read_text())
    except (OSError, ValueError):
        return None, f"{rec.name} is not readable"
    return (got, "") if isinstance(got, dict) else (None, f"{rec.name} is not a record")


def opens_on_nothing(acct, slot: dict) -> Tuple[bool, str]:
    """(True, the engine's reason it has no cold open) for a clip that opens on its headline alone, else (False, what
    opens it or why it is not known)."""
    if slot.get("vs_card"):
        return False, "it opens on its VS card"
    if slot.get("pre_hook"):
        return False, f"it opens on the pre-hook {slot['pre_hook'].get('name')}"
    rec, why = cold_record(acct, slot)
    if rec is None:
        return False, why
    if "skip" not in rec:
        return False, f"it opens on its cold open {str(rec.get('text') or '')[:60]!r}"
    return True, str(rec["skip"])[:300]


def fallback_on(acct, day: str) -> bool:
    """Is the fallback on for the board of `day` (YYYY-MM-DD)?"""
    from . import switches as SW
    if SW.mode(acct, "pre_hook.fallback") != "on":
        return False
    first = str(SW.settings(acct).get("pre_hook.fallback_from") or "")
    return not first or str(day) >= first


def in_test(acct, slot: dict) -> str:
    """The running pre-hook test this clip counts in ('' when none): its arm decides whether it has a pre-hook."""
    from . import learn as LR
    try:
        exps = json.loads(LR.experiments_path(acct).read_text())
    except (OSError, ValueError):
        return ""
    day, key = str(slot.get("publish_at") or "")[:10], str(slot.get("slot") or "")
    for eid, e in sorted((exps or {}).items()):
        if not isinstance(e, dict) or e.get("kind") != "pre_hook" or e.get("state") != "running":
            continue
        for arm in ("A", "B"):
            for p in ((e.get("posts") or {}).get(arm) or []):
                if p.get("board") == day and p.get("slot") == key and not p.get("practice"):
                    return f"{eid} arm {arm}"
    return ""


def fallback(cfg: C.Config, acct, slot: dict) -> Tuple[Optional[dict], str]:
    """At collect, after the test: THE OPENER RULE's pre-hook (Daily 8 step 3, 2026-10-29) on a calm start that the
    pre-hook test does not hold: no VS card, no shout, no fight and no strong line in the clip's own first 3 s
    (opener.calm; the cold open, when the engine made one, then plays after the pre-hook). Before the rule (10-29)
    it went on any clip with no cold open. (record, "") when it went on, else (None, why not); the clip is never
    lost over it (apply puts every file back)."""
    from . import opener as OP
    if not fallback_on(acct, str(slot.get("publish_at") or "")[:10]):
        return None, "the pre_hook.fallback switch is off for this board"
    if slot.get("vs_card"):
        return None, "it opens on its VS card"
    if slot.get("pre_hook"):
        return None, f"it opens on the pre-hook {slot['pre_hook'].get('name')}"
    held = in_test(acct, slot)
    if held:
        return None, f"the pre-hook test decides this clip ({held})"
    ok, why = OP.calm(acct, slot)
    if not ok:
        return None, f"not a calm start: {why}"
    rec, err = apply(cfg, acct, slot)
    if rec is None:
        B.note(slot, f"no pre-hook for its calm start: {err}")
        return None, err
    rec["fallback"] = why
    B.note(slot, f"the pre-hook is its opener: a calm start ({why[:160]})")
    return rec, ""


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """One line per owner account: the fallback switch, from which board, and what it does. Read only."""
    from . import switches as SW
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in cfg.accounts.values():
        if SW._is_client(a):
            continue
        first = str(SW.settings(a).get("pre_hook.fallback_from") or "")
        if SW.mode(a, "pre_hook.fallback") == "on":
            out.append((None, f"{a.name} pre_hook.fallback: on{' from the ' + first + ' board' if first else ''} "
                              f"(the opener rule: a calm start outside the pre-hook test opens on a pre-hook)", ""))
        else:
            out.append((None, f"{a.name} pre_hook.fallback: off; a clip with no cold open opens on its headline. "
                              f"On: factory.py social set {a.slug} pre_hook.fallback_from YYYY-MM-DD, then "
                              f"factory.py social on {a.slug} pre_hook.fallback", ""))
    return out
