"""THE COVER ART (Andrew 2026-10-30, "Yes, build it"): a designed cover for every clip, made at collect beside the
clip in its draft, shown on its Clip Review card, and set as the Reel's cover where a platform takes a picture.

Why: the covers were frames of the clip (covers.py): a caption word mid-screen, a small headline, Garage's end card.
Andrew: "if someone goes to my account, they scroll, they'll be like, oh wow, this is like a professional level news
organization". The looks are his picks from the mockups (Desktop/CLIP FACTORY/Factory/Projects/Covers 10.30/Mockups).

    Politics   THE POLITICS, colour ("Color looks way better ... That's how it should look"): the subject's face
                     clean on top, from the master (no captions; borders trimmed, the top eighth and bottom quarter
                     cut, where another channel's dateline, lower third and bug live), a white hairline, then on
                     black a small grey line over the big words, who said them, the POLITICS lockup in the
                     same corner every time. Type black, white and grey only ("Stop with the gold"), one 80 px margin,
                     curly apostrophes, no line ending on THE or A, nothing within reach of an edge ("that quote's on
                     the very edge of it").
                     The words: the speaker's exact words in quote marks with his name, only when the night author
                     wrote them on the slate (cover_quote + cover_who: one speaker, sure); else the clip's headline
                     (its hook) under the source and date, no quote marks, no name. Never words put in a mouth: a
                     two-person clip's cold open can be either voice (10-30: Gabbard's was hers and Bennet's at once,
                     Vance's the moderator's), and Tapper-Miller's slate "quote" was Tapper's though only Miller is
                     carded. The face: cover_face's, else the speaker's, else the witness's card (the person a
                     headline is about), at the second the engine checked that face, else the poster's second.
                     A fight keeps its VS card (covers.vs_moment): no art.
    Gaming,       BIG TITLE ("I like the big titles. The big titles are really good"): the clip's picture at its
    Stunts      poster second from the source over the grid's part, black above and below with a pink rule, the
                     title huge in Anton, white with the pink outline (cover_title, else the hook, sworn words
                     starred); the streamer's face in a white box when the stream shows one.
    Cars     SPEC SHEET (his pick 10.30, "A: Spec sheet"): the car big from its own cleanest shot (the
                     maker's best scored segment with no on-screen text, from the pulled footage), a red rule, then on
                     black the price and top speed small, the car's name huge and wide (Archivo), the format in red
                     (TOP SPEED, WHERE TO FIND IT, THE EDIT, VS <rival>). The maker's prep.json has all of it.

Instagram's profile grid shows the middle 3:4 of a cover (y 240-1680): everything that must read sits there.

The switch, per account, off unless on (state/<account>/social/settings.json, switches.py):

    factory.py social on politics cover_art

Art never stops a collect or a post: no master, no picture, no font, any error, and the slot has no art, the cover
is the poster frame (covers.py) as before, and the slot's notes say why.
"""
from __future__ import annotations

import glob
import itertools
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from . import config as C

SWITCH = "cover_art"
NAME = "cover-art.jpg"
W, H = 1080, 1920
SAFE = (240, 1680)                       # Instagram's profile grid shows y 240-1680 of a 9:16 cover
M = 80                                   # THE POLITICS's one margin
HN = "/System/Library/Fonts/HelveticaNeue.ttc"
HN_INDEX = {"hn-bold": 1, "hn-cblack": 9, "hn-med": 10}
ANTON = Path.home() / ".claude/skills/gaming/assets/fonts/Anton-Regular.ttf"
POLITICS_JOBS = Path.home() / "Desktop" / "Clipping" / "Jobs"
GTA_JOBS = {"gaming": Path.home() / "Desktop" / "Andrew Johnson" / "Gaming" / "Jobs",
            "stunts": Path.home() / "Desktop" / "Andrew Johnson" / "Stunts" / "Jobs"}
LOOKS = {"politics": "politics", "gaming": "big_title", "stunts": "big_title", "cars": "spec"}
ARCHIVO = C.ROOT / "garage" / "engine" / "fonts" / "Archivo.ttf"

INK, BONE, GREY = (11, 11, 11), (244, 241, 234), (168, 168, 168)
PINK, WHITE, RED = (255, 46, 147), (255, 255, 255), (227, 38, 46)
QUOTE_SIZES = (150, 134, 120, 108)       # a short ladder: the grid reads as one size, not a different one per post
SWORN = re.compile(r"\b(f+u+c+k\w*|s+h+i+t\w*|b+i+t+c+h\w*|a+s+s+h+o+l+e\w*|d+i+c+k\w*|c+u+n+t\w*)\b", re.I)


# ------------------------------------------------------------------------------------------------------ the switch
def on(acct) -> bool:
    from . import switches as SW
    return SW.mode(acct, SWITCH) == "on"


def path_of(slot: dict) -> Optional[Path]:
    """The slot's cover art on disk, None when it has none."""
    rec = slot.get("cover_art") if isinstance(slot, dict) else None
    p = Path(str(rec.get("path"))) if isinstance(rec, dict) and rec.get("path") else None
    return p if p is not None and p.is_file() else None


def apply(cfg, acct, slot: dict) -> str:
    """At collect: make the slot's art and record it (slot["cover_art"]); what happened, for the notes."""
    slot.pop("cover_art", None)                # the last clip's: this one gets its own, or none
    if not on(acct):
        return ""
    look = LOOKS.get(acct.slug)
    if not look:
        return "no cover art: this account has no look yet"
    if slot.get("vs_card"):
        return "no cover art: a fight's cover is its VS card"
    out = Path(str(slot.get("folder") or "")) / NAME
    if not out.parent.is_dir():
        return "no cover art: the draft has no folder"
    if look == "politics":
        img, rec = politics(slot)
    elif look == "spec":
        img, rec = garage(acct, slot)
    else:
        img, rec = big_title(acct, slot)
    if img is None:
        return f"no cover art: {rec.get('why')}"
    img.save(out, quality=92)
    slot["cover_art"] = {**rec, "path": str(out), "look": look,
                         "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    return f"cover art made ({look}, {rec.get('mode', 'title')})"


# ------------------------------------------------------------------------------------------------------ pictures
def _faces():
    try:
        import cv2
        return cv2, cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except Exception:  # noqa: BLE001  (no face finder: the middle of the picture)
        return None, None


def face(img, prefer: str = ""):
    """(cx, cy, height) of the biggest face in `img`, None when there is none (or no face finder). With prefer
    "left" or "right" and two faces of about one size (a split screen), the one on that side."""
    cv2, cc = _faces()
    if cc is None:
        return None
    import numpy as np
    g = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    f = cc.detectMultiScale(g, 1.1, 6, minSize=(img.height // 14, img.height // 14))
    if not len(f):
        return None
    big = max(f, key=lambda r: r[2] * r[3])
    near = [r for r in f if r[3] >= 0.6 * big[3]]
    if prefer in ("left", "right") and len(near) >= 2:
        big = (max if prefer == "right" else min)(near, key=lambda r: r[0])
    x, y, w, h = big
    return x + w / 2, y + h / 2, h


def fill(img, bw: int, bh: int, focus=None, at=(0.5, 0.42), face_share=None):
    """`img` covering a bw x bh box, the focus (a face, else the middle) at `at` of the box; with face_share, zoomed
    (to 1.6x at most) until the face is that share of the box's height."""
    from PIL import Image, ImageFilter
    fx, fy = (focus[0], focus[1]) if focus else (img.width / 2, img.height / 2)
    base = max(bw / img.width, bh / img.height)
    s = base
    if focus and face_share:
        s = max(s, min(1.6 * base, face_share * bh / focus[2]))
    big = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x = min(max(fx * s - at[0] * bw, 0), big.width - bw)
    y = min(max(fy * s - at[1] * bh, 0), big.height - bh)
    out = big.crop((round(x), round(y), round(x) + bw, round(y) + bh))
    return out.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2)) if s > 1.05 else out


def trim(pic):
    """The picture without the source's own borders: edge columns or rows as flat as a wall (black bars, a white or
    grey strip), at most 15 % a side."""
    import numpy as np
    g = np.asarray(pic.convert("L").resize((max(pic.width // 4, 1), max(pic.height // 4, 1))), dtype=float)

    def edge(stds, n):
        k = 0
        while k < int(n * 0.15) and stds[k] < 7:
            k += 1
        return k
    cs, rs = g.std(axis=0), g.std(axis=1)
    l, r = edge(cs, len(cs)), edge(cs[::-1], len(cs))
    t, b = edge(rs, len(rs)), edge(rs[::-1], len(rs))
    if not (l or r or t or b):
        return pic
    pad = 2                                             # and the antialiased seam beside a bar
    return pic.crop(((l + pad) * 4 if l else 0, (t + pad) * 4 if t else 0,
                     pic.width - ((r + pad) * 4 if r else 0), pic.height - ((b + pad) * 4 if b else 0)))


def sharpest(src: str, t: float) -> Optional[object]:
    """The sharpest of five frames of `src` around second `t` (a blink or a whip pan loses), None when unreadable."""
    import io

    import numpy as np
    from PIL import Image
    best = None
    for dt in (-1.0, -0.5, 0.0, 0.5, 1.0):
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(t + dt, 0):.2f}", "-i", str(src),
                            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "2", "-"],
                           capture_output=True, timeout=60)
        if r.returncode or len(r.stdout) < 1000:
            continue
        im = Image.open(io.BytesIO(r.stdout)).convert("RGB")
        g = np.asarray(im.convert("L"), dtype=float)
        lap = (g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]).var()
        if best is None or lap > best[0]:
            best = (lap, im)
    return best[1] if best else None


def best_frame(src: str, times: List[float], prefer: str = ""):
    """(picture, second): of the frames of `src` at `times`, one with a face first, the biggest (the one talking, not
    a man behind him), a whole one before one an edge cuts, then the sharpest; (None, None) when none was read."""
    import io

    import numpy as np
    from PIL import Image
    best = None
    for t in times:
        r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(t, 0):.2f}", "-i", str(src),
                            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "2", "-"],
                           capture_output=True, timeout=60)
        if r.returncode or len(r.stdout) < 1000:
            continue
        im = Image.open(io.BytesIO(r.stdout)).convert("RGB")
        g = np.asarray(im.convert("L"), dtype=float)
        lap = (g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]).var()
        f = face(im, prefer)
        if f is not None and f[2] < 0.09 * im.height:     # a face in the crowd or behind him: not the speaker's
            f = None
        whole = f is not None and f[0] - 0.6 * f[2] > 0 and f[0] + 0.6 * f[2] < im.width and f[1] - 0.7 * f[2] > 0
        score = (1 if f else 0, (f[2] if whole else 0.6 * f[2]) if f else 0, lap)   # the speaker's big face
        if best is None or score > best[0]:
            best = (score, im, t)
    return (best[1], best[2]) if best else (None, None)


# ------------------------------------------------------------------------------------------------------ words
def font(kind: str, size: int):
    from PIL import ImageFont
    if kind == "anton":
        return ImageFont.truetype(str(ANTON), size)
    if kind.startswith("archivo"):
        f = ImageFont.truetype(str(ARCHIVO), size)
        f.set_variation_by_axes([900, 125] if kind == "archivo-wide" else [650, 100])
        return f
    return ImageFont.truetype(HN, size, index=HN_INDEX[kind])


def typo(s: str) -> str:
    """Printer's apostrophes and quote marks: straight ones read as a typewriter, not a newsroom."""
    out, opened = [], False
    for ch in s.replace("'", "’"):
        if ch == '"':
            out.append("”" if opened else "“")
            opened = not opened
        else:
            out.append(ch)
    return "".join(out)


def width(f, s: str, stroke: int = 0) -> int:
    b = f.getbbox(s, stroke_width=stroke)
    return b[2] - b[0]


def fit(text: str, kind: str, max_w: int, max_lines: int, hi: int, lo: int, stroke=lambda s: 0,
        mode: str = "balanced", strict: bool = False):
    """(font, lines): the biggest size at which `text` fits max_w in at most max_lines. "balanced" (a centred title)
    evens the lines; "fill" (left-set type) fills them like a column, never one word alone on the last line of a
    five-word line or more, never THE/A/AN at a line end, a headline's colon always at one (strict: such a break is
    no fit at all)."""
    words = typo(text).split()
    for size in range(hi, lo - 1, -4):
        f, sw = font(kind, size), stroke(size)
        for n in range(1, min(max_lines, len(words)) + 1):
            best = None
            for cuts in itertools.combinations(range(1, len(words)), n - 1):
                parts = [" ".join(words[a:b]) for a, b in zip((0,) + cuts, cuts + (len(words),))]
                ws = [width(f, p, sw) for p in parts]
                if max(ws) > max_w:
                    continue
                if mode == "fill":
                    score = (sum((max_w - x) ** 2 for x in ws[:-1])
                             + (10 ** 12 if n > 1 and len(words) >= 5 and " " not in parts[-1] else 0)
                             + 10 ** 9 * sum(p.split()[-1].strip("“”") in ("THE", "A", "AN")
                                             for p in parts[:-1])
                             + 10 ** 9 * any(wd.endswith(":") for p in parts for wd in p.split()[:-1])
                             + 10 ** 9 * any(x < max_w / 3 for x in ws[1:-1]))      # no lone word mid-block
                    if strict and score >= 10 ** 9:
                        continue
                else:
                    score = max(ws)
                if best is None or score < best[0]:
                    best = (score, parts)
            if best:
                return f, best[1]
    return font(kind, lo), [typo(text)]


def tracked(d, xy, s, f, fill_, track) -> float:
    x, y = xy
    for ch in typo(s):
        if d is not None:
            d.text((x, y), ch, font=f, fill=fill_)
        x += width(f, ch) + track if ch != " " else width(f, "n") * 0.6 + track
    return x


def small_caps(d, xy, s, kind, size, fill_, track, max_w):
    """A tracked caps line that never runs past max_w: a size smaller until it fits."""
    while size > 18 and tracked(None, (0, 0), s, font(kind, size), None, track) > max_w:
        size -= 1
    tracked(d, xy, s, font(kind, size), fill_, track)


def clean(title: str) -> str:
    """A title for a cover: sworn words starred (F***ING), the rest as written."""
    return SWORN.sub(lambda m: m.group(0)[0] + "*" * 3 + (m.group(0)[4:] if len(m.group(0)) > 4 else ""), title)


# ------------------------------------------------------------------------------------------------------ the Politics
def _politics_job(slot: dict) -> Optional[Path]:
    clip = Path(str((slot.get("clip") or {}).get("path") or ""))
    hits = glob.glob(str(POLITICS_JOBS / "*" / "clips" / clip.name)) if clip.name else []
    return Path(hits[0]).parent.parent if hits else None


def _master_second(c: dict, t_clip: float, cold: float) -> float:
    """The master's second for the clip's second t_clip: past the cold open (cold seconds in front), along the
    slate's segments."""
    t, cum = t_clip - cold, 0.0
    for a, b in c.get("segments") or []:
        if t < cum + (b - a):
            return a + max(t - cum, 0.0)
        cum += b - a
    seg = (c.get("segments") or [[0, 0]])[0]
    return seg[0]


def _when(job: Path) -> str:
    """'OCT 29, 2026' from a job folder named '... 10.29.26', '' when it has no date."""
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2})$", job.name)
    if not m:
        return ""
    try:
        return datetime(2000 + int(m.group(3)), int(m.group(1)), int(m.group(2))).strftime("%b %-d, %Y").upper()
    except ValueError:
        return ""


def politics_words(slot: dict, job: Path, c: dict, cards: List[dict]) -> dict:
    """What THE POLITICS says: quote mode only from the author's own cover_quote + cover_who (one speaker, sure),
    else the headline under the source and date."""
    hook = str(c.get("hook") or "").strip()
    q, who = str(c.get("cover_quote") or "").strip(), str(c.get("cover_who") or "").strip()
    if q and who:
        card = next((k for k in cards if str(k.get("name", "")).lower() == who.lower()), None)
        title = str((card or {}).get("title") or "").strip()
        return {"mode": "quote", "kicker": hook, "quote": q.strip("\"“”"),
                "who": who + (", " + title.title() if title else ""), "face_of": str(c.get("cover_face") or who)}
    src = str(slot.get("host") or c.get("credit") or "").strip()
    when = _when(job)
    kicker = " · ".join(x for x in (src, when) if x) or "POLITICS"
    up = hook.upper()
    named = [k for k in cards if str(k.get("name") or "").split() and str(k["name"]).split()[-1].upper() in up]
    witness = next((k for k in cards if k.get("role") == "witness"), cards[0] if cards else None)
    if named:                                 # the person the headline names first is the one it is about
        witness = min(named, key=lambda k: up.index(str(k["name"]).split()[-1].upper()))
    if when:                                  # "2025: GABBARD ..." under "PBS NEWSHOUR · JAN 30, 2025": once
        hook = re.sub(r"^\d{4}:\s*", "", hook)
    return {"mode": "headline", "kicker": kicker, "headline": hook,
            "face_of": str(c.get("cover_face") or (witness or {}).get("name") or "")}


def politics(slot: dict) -> Tuple[Optional[object], dict]:
    """(THE POLITICS cover, what it is) for a Politics slot, (None, {"why": ...}) when it cannot be made."""
    job = _politics_job(slot)
    if job is None:
        return None, {"why": "the clip's engine job was not found"}
    clip_name = Path(str(slot["clip"]["path"])).name
    stem = clip_name[:-4]
    p = json.loads((job / "project.json").read_text())
    master = str(p.get("source") or "")
    if not Path(master).is_file():
        return None, {"why": "the master is gone"}
    sl = json.loads((job / "slate.json").read_text())
    clips = sl.get("clips") if isinstance(sl, dict) else sl
    slug = stem[3:] if re.match(r"^\d\d-", stem) else stem
    c = next((x for x in clips or [] if x.get("slug") == slug), None)
    if c is None or not c.get("hook"):
        return None, {"why": "the slate has no hook for this clip"}
    cp = job / "clips" / f"{stem}.cards.json"
    cards = (json.loads(cp.read_text()).get("cards") or []) if cp.is_file() else []
    co = job / "clips" / f"{stem}.coldopen.json"
    cold = float(json.loads(co.read_text()).get("d") or 0) if co.is_file() else 0.0
    words = politics_words(slot, job, c, cards)
    card = next((k for k in cards if str(k.get("name", "")).lower() == words["face_of"].lower()), None)
    # two people on a split screen: the asker sits left, the one answering right (the VS card's rule)
    prefer = "" if len(cards) <= 1 or card is None else ("right" if card.get("role") == "witness" else "left")
    pic, t = None, None
    # The rendered clip's clock is not the master's past its first seconds (silence cuts, speed, the cold open), so
    # a late second maps to the wrong shot (10-30 tests: Walz on Vance's, flags on Helsinki's). One person on the
    # cards: the engine's own poster second, early in the clip, from the first segment (its face). Two: the
    # subject's checked second (SFace, the cast check), as the poster may hold the other one (Bennet on Gabbard's,
    # Piers Morgan on Zacharia's); a tight window either side, never across a cut.
    if len(cards) > 1 and card and card.get("check_at") is not None:
        t0 = _master_second(c, float(card["check_at"]), cold)
        pic, t = best_frame(master, [t0 + k * 0.25 for k in range(-2, 3)], prefer)
    if pic is None or face(pic, prefer) is None:
        p0 = float(((c.get("segments") or [[0, 0]])[0])[0]) + float(c.get("poster_at") or 2.0)
        alt, at = best_frame(master, [p0 + k * 0.5 for k in range(-3, 4)], prefer)
        if alt is not None and (pic is None or face(alt, prefer) is not None):
            pic, t = alt, at
    if (pic is None or face(pic, prefer) is None) and card and card.get("check_at") is not None:
        # no face at the poster (10-29: the State of the Union title card on Tapper-Miller's): the checked second
        tc = float(card["check_at"])
        bases = {_master_second(c, tc, cold), float(((c.get("segments") or [[0, 0]])[0])[0]) + tc - cold}
        alt, at = best_frame(master, sorted(b + k * 0.5 for b in bases for k in range(-2, 3)), prefer)
        if alt is not None and face(alt, prefer) is not None:
            pic, t = alt, at
    if pic is None:
        return None, {"why": "no frame of the master"}
    if face(pic, prefer) is None:             # a title card, a wide shot: the poster frame is the better cover
        return None, {"why": "no face at the poster's second or the checked one"}
    return politics_paper(pic, words, prefer), {"mode": words["mode"], "words": words, "from": f"master {t:.2f}s"}


def politics_masthead(d, x, y):
    d.rectangle([x, y, x + 6, y + 62], fill=BONE)
    f = font("hn-bold", 27)
    tracked(d, (x + 18, y - 2), "POLITICS", f, BONE, 5)
    tracked(d, (x + 18, y + 31), "POLITICS", f, BONE, 5)


def _pull(d, w: dict, x: int, y: int, max_w: int, who_w: int) -> int:
    """The small grey line, the big words (curly quote marks in quote mode), who said them; the y under it."""
    small_caps(d, (x, y), w["kicker"].upper(), "hn-bold", 32, GREY, 5, max_w)
    q = w.get("quote")
    text = "“" + q.upper() + "”" if q else w["headline"].upper()
    f, lines = None, []
    for strict in (True, False):                  # clean breaks at any size first, then the best at any break
        for i, size in enumerate(QUOTE_SIZES):
            f, lines = fit(text, "hn-cblack", max_w, 3 if i < len(QUOTE_SIZES) - 1 else 4, size, size,
                           mode="fill", strict=strict)
            if max(width(f, ln) for ln in lines) <= max_w:
                break
        else:
            continue
        break
    asc, desc = f.getmetrics()
    step = round((asc + desc * 0.15) * 0.93)
    y += 56
    for ln in lines:
        d.text((x, y), ln, font=f, fill=BONE)
        y += step
    if q and w.get("who"):
        small_caps(d, (x, y + 30), w["who"].upper(), "hn-med", 29, GREY, 4, who_w)
    return y + 30 + 34


def politics_paper(pic, words: dict, prefer: str = ""):
    """THE POLITICS: the face on top (790 px of the grid), a hairline, the words on black, the lockup bottom right."""
    from PIL import Image, ImageDraw
    pic = trim(pic.convert("RGB"))
    pic = pic.crop((0, round(pic.height * 0.12), pic.width, round(pic.height * 0.74)))
    im = Image.new("RGB", (W, H), INK)
    im.paste(fill(pic, W, 790, face(pic, prefer), at=(0.5, 0.46), face_share=0.30), (0, SAFE[0]))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 1030, W, 1032], fill=BONE)
    _pull(d, words, M, 1070, W - 2 * M, W - 2 * M - 200)
    f = font("hn-bold", 27)                       # the lockup's right edge on the margin, like the words' left
    lw = 18 + max(tracked(None, (0, 0), w_, f, None, 5) - 5 for w_ in ("POLITICS", "POLITICS"))
    politics_masthead(d, W - M - round(lw), 1596)
    return im


# ------------------------------------------------------------------------------------------------------ BIG TITLE
def _gta_job(acct, slot: dict) -> Optional[Path]:
    root = GTA_JOBS.get(acct.slug)
    slug = str(slot.get("slug") or "")
    if root is None or not slug:
        return None
    hits = [h for h in glob.glob(str(root / "*" / "clips" / f"*{slug}.mp4")) if "/_first/" not in h]
    return Path(hits[0]) if hits else None


def big_title(acct, slot: dict) -> Tuple[Optional[object], dict]:
    """(BIG TITLE cover, what it is) for a Gaming or Stunts slot, (None, {"why": ...}) when it cannot be
    made."""
    from PIL import Image, ImageDraw
    clip = _gta_job(acct, slot)
    if clip is None:
        return None, {"why": "the clip's job was not found"}
    proj = clip.parent.parent / "_project"
    p = json.loads((proj / "project.json").read_text())
    src = str(p.get("source") or "")
    if not Path(src).is_file():
        found = glob.glob(src + "*.mp4") or glob.glob(str(Path(src) / "*.mp4"))
        src = found[0] if found else ""
    if not src:
        return None, {"why": "the source video is gone"}
    sl = json.loads((proj / "slate.json").read_text())
    clips = sl.get("clips") if isinstance(sl, dict) else sl
    c = next((x for x in clips or [] if x.get("slug") == slot.get("slug")), None)
    if c is None:
        return None, {"why": "the slate has no entry for this clip"}
    title = clean(str(c.get("cover_title") or c.get("hook") or "").strip())
    if not title:
        return None, {"why": "the clip has no title"}
    posters = json.loads((proj / "posters.json").read_text()) if (proj / "posters.json").is_file() else {}
    t = float(c.get("start") or 0) + float(posters.get(clip.stem, 1.5))
    pic = sharpest(src, t)
    if pic is None:
        return None, {"why": f"no frame of the source at {t:.1f}s"}
    pic = trim(pic)                                    # the source's own letterbox and borders
    im = Image.new("RGB", (W, H), INK)                 # black above and below the grid's part: no soft stretch
    im.paste(fill(pic, W, SAFE[1] - SAFE[0], None, at=(0.5, 0.36)), (0, SAFE[0]))
    d = ImageDraw.Draw(im)
    d.rectangle([0, SAFE[0] - 8, W, SAFE[0]], fill=PINK)
    d.rectangle([0, SAFE[1], W, SAFE[1] + 8], fill=PINK)
    sw = lambda s: max(8, round(s * 0.085))  # noqa: E731
    f, lines = fit(title.upper(), "anton", W - 120, 3, 230, 120, sw)
    asc, desc = f.getmetrics()
    step = round((asc + desc * 0.15) * 0.92)
    y = 1640 - step * len(lines)
    for ln in lines:
        lw = width(f, ln, sw(f.size))
        d.text(((W - lw) / 2, y), ln, font=f, fill=WHITE, stroke_width=sw(f.size), stroke_fill=PINK)
        y += step
    handle = {"gaming": "@GAMING", "stunts": "@STUNTS"}.get(acct.slug, "")
    if handle:
        d.text((48, 282), handle, font=font("anton", 44), fill=WHITE, stroke_width=4, stroke_fill=PINK)
    fc = face(pic) if acct.slug == "stunts" else None     # the streamer, when the stream shows a face cam
    if fc:
        side = fc[2] * 3.0
        x0 = min(max(fc[0] - side / 2, 0), pic.width - side)
        y0 = min(max(fc[1] - side / 2.4, 0), pic.height - side)
        if x0 >= 0 and y0 >= 0:
            cam = fill(pic.crop((round(x0), round(y0), round(x0 + side), round(y0 + side))), 380, 380)
            im.paste(Image.new("RGB", (396, 396), WHITE), (40, 352))
            im.paste(cam, (48, 360))
    return im, {"mode": "title", "words": {"title": title}, "from": f"source {t:.2f}s"}


# ------------------------------------------------------------------------------------------------------ SPEC SHEET
def _garage_prep(acct, slot: dict) -> Tuple[Optional[Path], Optional[dict]]:
    """(the Garage maker's work folder for this slot's car, its prep.json): the slot's own <day>/<HHMM> folder when
    its car is the clip's, else any folder of that day whose car is (10-30: the 13:05 Grotti X80 Proto was made in
    spare1 while 1305 held a Cheval Taipan); (None, None) when no prep names this car: never another car's cover."""
    from . import garage as G
    day, key = str(slot.get("publish_at") or "")[:10], str(slot.get("slot") or "").replace(":", "")
    slug = str(slot.get("slug") or "")
    root = G.home(acct) / "work" / day
    if not day or not slug or not root.is_dir():
        return None, None
    for p in ([root / key] if key else []) + sorted(x for x in root.iterdir() if x.is_dir()):
        f = p / "prep.json"
        if not f.is_file():
            continue
        try:
            prep = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        car = str((prep.get("vehicle") or {}).get("slug") or "")
        if car and car in slug:
            return p, prep
    return None, None


def money(n) -> str:
    """$4.35M, $850K: the price as the Garage's end card says it."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    return f"${n / 1e6:.2f}M" if n >= 1e6 else f"${n / 1e3:.0f}K" if n >= 1e3 else (f"${n:.0f}" if n > 0 else "")


def garage_words(prep: dict) -> dict:
    v = prep.get("vehicle") or {}
    fmt = str(prep.get("format") or "").strip().upper()
    rival = (prep.get("rival") or {}).get("name") if isinstance(prep.get("rival"), dict) else None
    kind = f"VS {rival}".upper() if fmt == "VS" and rival else fmt
    top = v.get("top_mph")
    stats = [x for x in (money(v.get("price")), f"{float(top):.1f} MPH" if top else "") if x]
    return {"name": str(v.get("name") or "").strip(), "kind": kind,
            "kicker": "  ·  ".join(["CARS"] + stats)}


def _hero(prep: dict):
    """(picture, source second, crop centre 0-1) of the car's best shot: the edit's own hero shots (its beauty shots),
    then its hook, framed where the maker framed them (cx); the sharpest wins, a frame with a person in it loses
    (the car is the subject: 10-30's HVY Menacer picked a man walking past it); (None, None, 0.5) when none reads."""
    import io

    import numpy as np
    from PIL import Image
    best = None
    shots = sorted((x for x in prep.get("shots") or [] if x.get("role") in ("hero", "hook")),
                   key=lambda x: x.get("role") != "hero")
    for sh in shots:
        s = (prep.get("sources") or {}).get(sh.get("s")) or {}
        t0 = float(sh.get("t") or 0)
        win = next((w for w in s.get("windows") or [] if len(w) > 2 and float(w[0]) <= t0 + 0.5 <= float(w[1])
                    and Path(str(w[2])).is_file()), None)
        if win is None:
            continue
        for dt in (0.3, 0.8):
            r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(t0 + dt - float(win[0]), 0):.2f}",
                                "-i", str(win[2]), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg",
                                "-q:v", "2", "-"], capture_output=True, timeout=60)
            if r.returncode or len(r.stdout) < 1000:
                continue
            im = Image.open(io.BytesIO(r.stdout)).convert("RGB")
            g = np.asarray(im.convert("L"), dtype=float)
            lap = (g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]).var()
            score = lap * (0.35 if face(im) is not None else 1.0) * (1.2 if sh.get("role") == "hero" else 1.0)
            if best is None or score > best[0]:
                best = (score, im, t0 + dt, float(sh.get("cx") or 0.5))
    return (best[1], best[2], best[3]) if best else (None, None, 0.5)


def garage(acct, slot: dict) -> Tuple[Optional[object], dict]:
    """(SPEC SHEET cover, what it is) for a Cars slot, (None, {"why": ...}) when it cannot be made."""
    work, prep = _garage_prep(acct, slot)
    if prep is None:
        return None, {"why": "no Garage maker's prep names this clip's car (a clip dropped by hand)"}
    words = garage_words(prep)
    if not words["name"]:
        return None, {"why": "the prep has no car name"}
    pic, t, cx = _hero(prep)
    if pic is None:
        return None, {"why": "no shot of the car could be read"}
    return garage_paper(pic, words, cx), {"mode": "spec", "words": words, "from": f"{work.name} source {t:.2f}s"}


def garage_paper(pic, words: dict, cx: float = 0.5):
    """SPEC SHEET: the car over the top 1290 px (near its own pixels, centred where the edit centres it), a red rule,
    then on black the format in red, the name huge and wide, the price and top speed; all inside the grid's part."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), INK)
    pic = trim(pic.convert("RGB"))
    im.paste(fill(pic, W, 1290, (cx * pic.width, pic.height * 0.5, pic.height), at=(0.5, 0.58)), (0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 1290, W, 1300], fill=RED)
    y = 1330
    if words.get("kind"):
        small_caps(d, (M, y), words["kind"], "archivo", 38, RED, 4, W - 2 * M)
        y += 58
    f, lines = fit(words["name"].upper(), "archivo-wide", W - 2 * M, 2, 130, 70, mode="fill")
    asc, desc = f.getmetrics()
    step = round((asc + desc * 0.15) * 0.95)
    for ln in lines:
        d.text((M, y), ln, font=f, fill=WHITE)
        y += step
    small_caps(d, (M, min(y + 14, SAFE[1] - 42)), words["kicker"], "archivo", 30, (200, 200, 200), 3, W - 2 * M)
    return im


# ------------------------------------------------------------------------------------------------------ the doctor
def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """One line per owner account: the cover_art switch, its look and, when on, whether the fonts and the face finder
    are here. Read only."""
    from . import switches as SW
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in cfg.accounts.values():
        if SW._is_client(a):
            continue
        look = {"politics": "THE POLITICS", "big_title": "BIG TITLE"}.get(LOOKS.get(a.slug, ""), "")
        if not on(a):
            out.append((None, f"{a.name} cover art: off; covers are the clip's poster frame. On: factory.py social on "
                              f"{a.slug} cover_art" + (f" ({look})" if look else " (no look yet)"), ""))
            continue
        missing = [n for n, p in (("Helvetica Neue", Path(HN)), ("Anton", ANTON)) if not p.is_file()]
        cv2, _ = _faces()
        said = (f"{a.name} cover art: on ({look or 'no look yet: the poster frame'}; made at collect, set on "
                f"Facebook and Instagram)")
        if missing or cv2 is None:
            out.append((False if missing else None, said + f"; missing: {', '.join(missing) or 'the face finder (cv2)'}",
                        "fonts: Helvetica Neue ships with macOS; Anton with the gaming skill; cv2: pip install "
                        "opencv-python-headless" if missing or cv2 is None else ""))
        else:
            out.append((True, said, ""))
    return out
