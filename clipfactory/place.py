"""Place one finished clip into one named slot, as a draft that waits for Approve.

    factory.py place <account> --date D --slot HH:MM --clip X.mp4 --words W [--cover X.jpg]
                     [--source-url U ...] [--format F] [--host NAME] [--why TEXT] [--check]
    factory.py place gaming --date D --slot 19:15 --job JOB --slug SLUG [--why TEXT] [--check]
    factory.py place gaming --date D --slot 19:15 --release [--why TEXT]

The words (W): a .txt (first line the title, the rest the caption, for every platform: the drop folder's shape), or
a folder of paste files per platform (youtube.txt, tiktok.txt, reels.txt or instagram.txt, facebook.txt: the skills'
delivery shape). A delivery folder's own platform cuts (instagram.mp4 ...) come along, and its main .mp4 is the clip
when --clip is not given. With --job and --slug (Gaming) the skill's own copy engine writes the four paste files
from that job's slate, as the nightly pack does, and the clip, cover, source links, host and rights come from the job.

It never signs, schedules or posts: the clip lands READY and waits for Approve in Clip Review like any other draft.
It refuses, and writes nothing, when:
  - the slot is not open or failed (a clip, a sign-off or a running maker is in it); with --replace, when it does
    not hold an unsigned clip under the same slug (a newer render of that clip takes its place, said on the card)
  - the day already has this clip (its slug) in another slot that can still go out: one clip, one slot a day
    (moments.py, 2026-10-22: the night placed a newer Graham render into 18:20 beside its first one at 08:55)
  - the slot is less than 90 minutes away (time to watch it, and for YouTube's upload)
  - the clip is not an .mp4, or its length is outside the account's seconds
  - the clip is already on a board (its SHA-256, any account, any day), or is an iCloud copy ("NAME 2.mp4" beside
    a NAME.mp4)
  - a platform has no words
  - it would be a second REACTIONS clip on a day with one REACTIONS slot (reactions_slots in accounts.json)
The files are COPIED, never moved: into state/<account>/delivered/<date>/<HHMM slug>/ (the delivery shape collect
reads), then into the drafts exactly as collect takes a skill's delivery. The board keeps the source links (so the
nightly never cuts those videos again), who is on it, and a note.

--release (a REACTIONS slot only): no REACTIONS clip is coming that day, so the nightly fills the slot with a normal
clip (otherwise it waits until 03:30 on its own day, then a later pass of the night shift, the GTA second half or
the 03:45 retry fills it).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import collect as K
from . import config as C

LEAD = timedelta(minutes=90)
COPY_NAME = re.compile(r"^(?P<stem>.+) (?P<n>[2-9])(?P<ext>\.[A-Za-z0-9]+)$")    # iCloud: "NAME 2.mp4" .. " 9"
URL = re.compile(r"https?://\S+")
PROVENANCE = ("host", "rights", "source_url", "source_urls", "source_title", "source", "job", "pick", "pick_span",
              "picks_tried", "question_written", "placed", "reactions_released", "lab")   # lab: a Lab test's
FILLED = (B.READY, B.APPROVED, B.SCHEDULED)

# the skill's own copy engine, run on the skill's Python as makers.PACK runs it: the four paste files for one slate clip
WORDS = r"""
import json, re, sys
from datetime import date
from pathlib import Path
publish, job, slug, date_s, out = sys.argv[1:6]
sys.path.insert(0, publish)
import day_folder as DF
if DF.gc is None:
    sys.exit("the skill's copy engine (gtacopy) did not import: no words written")
clip, cfg = DF.clip_entry(job, slug)
if not clip:
    sys.exit(f"job {job!r} has no clip {slug!r} on its slate")
packs = DF.gc.packs(clip, cfg, date.fromisoformat(date_s))
DF.paste_files(Path(out), clip, cfg, packs)
urls = []
for s in [cfg.get("source_url")] + list(cfg.get("sources") or []):
    u = s.get("url") if isinstance(s, dict) else s
    for m in re.findall(r"https?://\S+", str(u or "")):
        if m not in urls:
            urls.append(m)
host = cfg.get("host") if isinstance(cfg.get("host"), dict) else {}
print(json.dumps({"sources": urls, "host": str(host.get("name") or cfg.get("source_channel") or ""),
                  "rights": str(cfg.get("rights") or ""), "title": str(cfg.get("source_title") or ""),
                  "out_dir": str(cfg.get("out_dir") or ""), "template": str(clip.get("template") or ""),
                  "question_default": bool((packs.get("tiktok") or {}).get("question_default"))}))
"""


class Refused(Exception):
    """Why a placement was refused (nothing was written)."""


def reactions_slots(acct: C.Account, day: date) -> List[str]:
    """The account's REACTIONS slots on that day (accounts.json "reactions_slots", one or two slot times, while
    "reactions_until" has not passed). Empty for an account without them."""
    from . import makers as M
    return M.reactions_slots(acct, day)


def _icloud_copy(p: Path) -> str:
    """The original's name when p is iCloud's "NAME 2.ext" beside a NAME.ext ('' otherwise)."""
    m = COPY_NAME.match(p.name)
    if not m:
        return ""
    orig = p.with_name(m.group("stem") + m.group("ext"))
    return orig.name if orig.exists() else ""


def _slug(name: str) -> str:
    s = re.sub(r"^\d{1,2}[-_ ]", "", name)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "clip"


def on_boards(cfg: C.Config, sha: str) -> str:
    """Where a clip with this SHA-256 already sits ('' when nowhere): every account's every board, clip and cuts."""
    for acct in cfg.accounts.values():
        for bp in B.all_boards(acct):
            try:
                slots = json.loads(bp.read_text()).get("slots") or {}
            except (OSError, ValueError):
                continue
            for s in slots.values():
                if sha in B.clip_shas(s):
                    return f"{acct.name} {bp.stem} {s.get('slot')} ({s.get('status')})"
    return ""


def skill_words(acct: C.Account, job: str, slug: str, day: date, out: Path, timeout: int = 180) -> dict:
    """The skill's copy engine writes the four paste files for one slate clip into `out`; returns what the job says
    about the clip (sources, host, rights, title, out_dir). Gaming only (its Publish/day_folder.py)."""
    from . import makers as M
    publish = acct.account_dir / "Publish"
    if not (publish / "day_folder.py").is_file():
        raise Refused(f"{acct.name} has no Publish/day_folder.py: --job works for Gaming; give --words instead")
    try:
        r = subprocess.run([M.PY_SKILLS, "-c", WORDS, str(publish), job, slug, day.isoformat(), str(out)],
                           capture_output=True, text=True, timeout=timeout, cwd=str(publish), env=M.gta_env(acct))
    except subprocess.TimeoutExpired:
        raise Refused(f"the skill's copy engine ran past {timeout} s") from None
    if r.returncode != 0:
        raise Refused("the skill's copy engine refused: " + ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1])
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise Refused("the skill's copy engine said nothing readable") from None


def _job_file(folder: Path, slug: str, ext: str) -> Optional[Path]:
    """The job's own file for a slug: NN-slug.ext, else slug.ext, else the one file holding the slug (None if none or
    more than one)."""
    if not folder.is_dir():
        return None
    exact = [p for p in folder.iterdir() if re.fullmatch(rf"(\d+-)?{re.escape(slug)}\.{ext}", p.name)]
    if len(exact) == 1:
        return exact[0]
    loose = [p for p in folder.glob(f"*{slug}*.{ext}") if p.name not in K._CUT_NAMES]
    return loose[0] if len(loose) == 1 else None


def _words_files(acct: C.Account, words: Path) -> Dict[str, str]:
    """paste file name -> text, one per platform the account posts to (refuses a platform with none)."""
    if words.is_dir():
        out, missing = {}, []
        for plat in acct.platforms:
            p = K.pick_copy(words, plat)
            if p is None:
                missing.append(plat)
                continue
            if _icloud_copy(p):
                raise Refused(f"{p.name} is iCloud's copy of {_icloud_copy(p)}: use the original")
            out[p.name] = p.read_text()
        if missing:
            raise Refused(f"no words for {', '.join(missing)} in {words}")
        return out
    if not words.is_file():
        raise Refused(f"no words file at {words}")
    if _icloud_copy(words):
        raise Refused(f"{words.name} is iCloud's copy of {_icloud_copy(words)}: use the original")
    text = words.read_text()
    if not text.strip():
        raise Refused(f"{words} is empty")
    return {names[0]: text for names in K.COPY_FILES.values()}     # one paste file per platform, as the drop folder


def _fmt(cfg: C.Config, acct: C.Account, day: date, board: dict, slot: dict, asked: Optional[str], template: str,
         now: datetime) -> str:
    """The slot's format after the placement: the one asked for; else the job clip's own template (a REACTIONS job's
    clip is `reactions`); else `reactions` on a slot the night is holding for one (makers.reactions_held); else the
    slot's own (a REACTIONS slot whose hold is over takes the format the board dealt it)."""
    from . import makers as M
    ok = set(acct.slot_formats) | set(acct.formats) | ({"reactions"} if acct.maker == "gta" else set())
    if asked:
        if asked not in ok:
            raise Refused(f"format {asked!r} is not one of {', '.join(sorted(ok))}")
        return asked
    t = str(template or "").strip().lower()
    if t == "reactions" or (t and t in ok):
        return t
    if t.startswith("gameplay") and "gameplay" in ok:
        return "gameplay"
    if slot["slot"] in M.reactions_held(cfg, acct, day, board, now=now):
        return "reactions"
    if slot.get("format") == "reactions":
        return M.rotation_format(acct, slot["slot"])
    return str(slot.get("format") or "")


def _reactions_room(acct: C.Account, day: date, board: dict, slot_key: str) -> None:
    """Never two REACTIONS clips on one day unless the day has two REACTIONS slots."""
    cap = max(1, len(reactions_slots(acct, day)))
    have = [k for k, s in board["slots"].items()
            if k != slot_key and s.get("format") == "reactions" and s.get("status") in FILLED + (B.MAKING,)]
    if len(have) >= cap:
        raise Refused(f"{day} already has {len(have)} REACTIONS clip(s) ({', '.join(sorted(have))}); "
                      f"the day has {cap} REACTIONS slot(s)")


def release(cfg: C.Config, acct: C.Account, day: date, slot_key: str, why: str = "", check: bool = False,
            now: Optional[datetime] = None) -> Tuple[bool, List[str]]:
    """No REACTIONS clip is coming for this slot: the nightly cuts it as a normal clip."""
    board = B.read(acct, day) or B.new_board(cfg, acct, day)
    s = board["slots"].get(slot_key)
    if s is None:
        return False, [f"  {acct.name} {day} has no slot {slot_key}: nothing written"]
    if slot_key not in reactions_slots(acct, day) and s.get("format") != "reactions":
        return False, [f"  {slot_key} is not a REACTIONS slot on {day}: nothing to release"]
    if s["status"] not in (B.OPEN, B.FAILED):
        return False, [f"  {slot_key} is {s['status']}: nothing to release"]
    if check:
        return True, [f"  {slot_key}: would be released for a normal clip (--check: nothing written)"]
    with B.edit(cfg, acct, day) as bd:
        t = bd["slots"][slot_key]
        t["reactions_released"] = True
        B.note(t, "no REACTIONS clip for this slot" + (f" ({why})" if why else "") + ": the nightly fills it with "
                  "a normal clip")
    return True, [f"  {slot_key}: released; the nightly fills it with a normal clip"]


def place(cfg: C.Config, acct: C.Account, day: date, slot_key: str, clip: Optional[Path] = None,
          words: Optional[Path] = None, cover: Optional[Path] = None, sources: Optional[List[str]] = None,
          why: str = "", fmt: Optional[str] = None, host: Optional[str] = None, job: Optional[str] = None,
          slug: Optional[str] = None, check: bool = False, now: Optional[datetime] = None,
          replace: bool = False) -> Tuple[bool, List[str]]:
    """(placed, lines). Refusals write nothing and say why."""
    tmp = Path(tempfile.mkdtemp(prefix="clipfactory-place-"))
    try:
        return _place(cfg, acct, day, slot_key, clip, words, cover, list(sources or []), why, fmt, host, job, slug,
                      check, now or cfg.now(), tmp, replace)
    except Refused as e:
        return False, [f"  REFUSED, nothing written: {e}"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fits(s: dict, replace: bool) -> bool:
    """The slot can take this placement: open or failed, or (replace) an unsigned clip it will give way to."""
    return s["status"] in (B.OPEN, B.FAILED) or (replace and s["status"] == B.READY and bool(s.get("clip")))


def _twin(board: dict, slot_key: str, slug: str) -> Optional[dict]:
    """Another slot of the day holding this clip (its slug) that can still go out: one clip, one slot a day."""
    for k, t in sorted(board["slots"].items()):
        if k != slot_key and t.get("slug") == slug and t.get("clip") and t.get("status") in (B.READY,) + B.SIGNED:
            return t
    return None


def _place(cfg, acct, day, slot_key, clip, words, cover, sources, why, fmt, host, job, slug, check, now, tmp,
           replace=False):
    board = B.read(acct, day) or B.new_board(cfg, acct, day)
    s = board["slots"].get(slot_key)
    if s is None:
        raise Refused(f"{acct.name} {day} has no slot {slot_key} (slots: {', '.join(board['slots'])})")
    if not _fits(s, replace):
        raise Refused(f"{slot_key} on {day} is {s['status']}: only an open or failed slot takes a clip"
                      + (" (--replace: only an unsigned clip gives way; a signed one: Pull it first)" if replace else
                         (" (a newer render of the clip it holds: --replace)" if s["status"] == B.READY else "")))
    at = datetime.fromisoformat(s["publish_at"])
    if at - now < LEAD:
        mins = int((at - now).total_seconds() // 60)
        raise Refused(f"{slot_key} on {day} is {mins} minutes away; a placed clip needs 90 (time to watch it before "
                      f"Approve, and for YouTube's upload)")

    # the words, the clip, the cover and where it came from
    info: dict = {}
    wfiles: Dict[str, str] = {}
    extra: List[Path] = []           # a delivery folder's platform cuts and their own words
    if job or slug:
        if not (job and slug):
            raise Refused("--job and --slug go together")
        if words:
            raise Refused("--job writes the words with the skill's copy engine: drop --words, or drop --job")
        info = skill_words(acct, job, slug, day, tmp)
        wfiles = _words_files(acct, tmp)
        jd = Path(info.get("out_dir") or "") if info.get("out_dir") else acct.account_dir / "Jobs" / job
        clip = clip or _job_file(jd / "clips", slug, "mp4")
        cover = cover or _job_file(jd / "clips", slug, "jpg")
        srt = _job_file(jd / "captions", slug, "srt")
        if clip is None:
            raise Refused(f"no single clip for {slug!r} in {jd / 'clips'}: say which with --clip")
    else:
        if not words:
            raise Refused("say where the words are: --words <.txt or folder of paste files>, or --job/--slug")
        words = Path(words).expanduser()
        wfiles = _words_files(acct, words)
        if words.is_dir():
            if clip is None:
                mains = [p for p in words.glob("*.mp4") if p.name not in K._CUT_NAMES]
                if len(mains) != 1:
                    raise Refused(f"{words} holds {len(mains)} main clips: say which with --clip")
                clip = mains[0]
            for names in K.CUT_FILES.values():
                for n in names:
                    if (words / n).is_file():
                        extra.append(words / n)
                        if (words / f"{Path(n).stem}.cut.txt").is_file():
                            extra.append(words / f"{Path(n).stem}.cut.txt")
        if clip is None:
            raise Refused("say which clip: --clip X.mp4")
        srt = None
    clip = Path(clip).expanduser()
    if not clip.is_file():
        raise Refused(f"no clip at {clip}")
    if clip.suffix.lower() != ".mp4":
        raise Refused(f"{clip.name} is not an .mp4 (collect reads .mp4 only): convert it first")
    for p in [clip] + ([Path(cover).expanduser()] if cover else []):
        if _icloud_copy(p):
            raise Refused(f"{p.name} is iCloud's copy of {_icloud_copy(p)} beside it: use the original "
                          f"(a copy would post the same clip twice)")
    if cover:
        cover = Path(cover).expanduser()
        if not cover.is_file():
            raise Refused(f"no cover at {cover}")
    else:
        cover = next((p for p in (clip.with_suffix(".jpg"), clip.with_suffix(".JPG")) if p.is_file()), None)
    srt = srt or next((p for p in (clip.with_suffix(".srt"),) if p.is_file()), None)
    secs = K.probe_seconds(clip)
    if secs is None:
        raise Refused(f"ffprobe could not read {clip.name}")
    if not acct.min_seconds - 0.05 <= secs <= acct.max_seconds + 0.05:
        raise Refused(f"{clip.name} is {secs:.1f}s; {acct.name} takes {acct.min_seconds:.0f} to "
                      f"{acct.max_seconds:.0f} seconds")
    sha = B.sha256_file(clip)
    where = on_boards(cfg, sha)
    if where:
        raise Refused(f"{clip.name} is already on a board: {where}")
    urls: List[str] = []
    for u in list(sources) + list(info.get("sources") or []):
        found = URL.findall(str(u or ""))
        if str(u or "").strip() and not found:
            raise Refused(f"--source-url {u!r} is not a link (http...)")
        urls += [m for m in found if m not in urls]
    use_fmt = _fmt(cfg, acct, day, board, s, fmt, str(info.get("template") or ""), now)
    if use_fmt == "reactions":
        _reactions_room(acct, day, board, slot_key)
    who = host if host is not None else str(info.get("host") or "")
    rights = str(info.get("rights") or ("unverified" if acct.maker == "gta" else ""))
    slug = (re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")[:60] or "clip") if slug else _slug(clip.stem)
    twin = _twin(board, slot_key, slug)
    if twin is not None:
        raise Refused(f"{day} already has {slug} on {twin['slot']} ({twin['status']}): one clip, one slot a day. "
                      + (f"A newer render of it goes into that slot: --slot {twin['slot']} --replace"
                         if twin["status"] == B.READY else
                         f"It is signed there: Pull {twin['slot']}, then place this one into it with --replace"))
    if replace and s["status"] == B.READY and s.get("slug") != slug:
        raise Refused(f"{slot_key} holds {s.get('slug')}, not {slug}: --replace only puts a newer render of the same "
                      f"clip in its place")
    hh = slot_key.replace(":", "")
    said = [f"  {acct.name} {day} {slot_key}: {clip.name} ({secs:.1f}s, {use_fmt or 'no format'}"
            + (f", {who}" if who else "") + f"), words for {len(wfiles)} platform file(s)"
            + (f", {len(urls)} source link(s)" if urls else ", no source link")
            + (f", cover {cover.name}" if cover else ", no cover")]
    if info.get("question_default"):
        said.append("  note: the comment question is the skill's stock one; change it before Approve if it does not fit")
    if check:
        return True, said + ["  --check: nothing written"]

    # stage the delivery (copies) and take it, all under the board's lock: a collect running now either listed the
    # folders before the stage existed, or waits for the lock and then finds the stage already seen
    stage = C.STATE / acct.slug / "delivered" / day.isoformat() / f"{hh} {slug}"
    with B.edit(cfg, acct, day) as bd:
        t = bd["slots"][slot_key]
        if not _fits(t, replace) or (replace and t["status"] == B.READY and t.get("slug") != slug):
            raise Refused(f"{slot_key} was just filled ({t['status']})")          # filled since the first look
        if _twin(bd, slot_key, slug) is not None:
            raise Refused(f"{slug} was just filled into another slot of {day}")
        was = dict(t.get("clip") or {}) if replace and t["status"] == B.READY else {}
        if use_fmt == "reactions":
            _reactions_room(acct, day, bd, slot_key)
        before = json.loads(json.dumps(t))
        done = False
        try:
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir(parents=True)
            name = f"{clip.stem}.mp4" if f"{clip.stem}.mp4" not in K._CUT_NAMES else f"{slug}.mp4"   # collect reads *.mp4
            shutil.copy2(clip, stage / name)
            for p in [cover, srt]:
                if p:
                    shutil.copy2(p, stage / (Path(name).stem + p.suffix.lower()))
            for p in extra:
                shutil.copy2(p, stage / p.name)
            for n, text in wfiles.items():
                (stage / n).write_text(text)
            if who or rights:
                (stage / "source.json").write_text(json.dumps({"host": who, "rights": rights}) + "\n")
            for k in PROVENANCE:
                t.pop(k, None)
            msg = K._take(cfg, acct, t, stage, K.drafts_dir(cfg, acct, day))
            if t.get("status") != B.READY:
                raise Refused(f"collect would not take it: {msg.strip()}")
            bd.setdefault("seen", []).append(K.delivery_key(stage))
            done = True
        finally:
            if not done:                                    # a refusal or a crash never changes the slot
                t.clear()
                t.update(before)
                shutil.rmtree(stage, ignore_errors=True)
        if use_fmt and use_fmt != before.get("format"):
            B.note(t, f"format {before.get('format')} -> {use_fmt}")
            t["format"] = use_fmt
        if urls:
            t["source_urls"] = urls
            if len(urls) == 1:
                t["source_url"] = urls[0]
        if info.get("title"):
            t["source_title"] = info["title"]
        if job:
            t["job"] = job
        if who:
            t["host"] = who
        t["source"] = "placed"
        t["placed"] = {"at": B.utcnow(), "from": str(clip), "why": why[:400]}
        if was:                                             # a newer render took the unsigned clip's place
            t["replaced"] = {"at": B.utcnow(), "was_sha256": was.get("sha256"), "was_seconds": was.get("seconds"),
                             "from": str(clip)}
            B.note(t, f"replaced by a newer render of the same clip ({secs:.1f}s; the earlier one was "
                      f"{float(was.get('seconds') or 0):.1f}s), placed by hand")
        B.note(t, "placed" + (f" ({why[:300]})" if why else "") + ": waits for Approve in Clip Review"
               + (f"; sources: {', '.join(urls)[:400]}" if urls else ""))
    return True, said + [f"  {slot_key}: ready to approve in Clip Review (not signed, not scheduled)"]
