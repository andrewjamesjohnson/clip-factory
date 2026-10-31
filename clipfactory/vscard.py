"""THE VS CARD (Andrew, 2026-10-28: "Build ... the VS card opener ... Proofs first, then I'll swap them in"): a short
fighting-game opener in front of a Politics clip that is a fight between two named people. The look is the one he
chose (Desktop/CLIP FACTORY/Fun Intros 10.28, v3 FACE-OFF), as he cut it down on the first proofs (v4, 10.28: "I
don't know if I like the health bars ... we don't need round one ... I don't like the background ... the sound
sucks ... the cards ... should definitely be like what the video is"):

and as he sharpened it on v4 (v5, 10.28: "when it says Warren versus Stumpf ... the versus comes in as they said
... I also want ... the background to kind of merge together too, and then those two come in ... The background
still looks kind of shitty and low quality. Upgrade it ... be creative. But the titles and all the wording is great"):

    0.0   the asker's red and the answerer's navy slide in from the two sides and meet on the seam with a flash
    ~0.3  the asker's card cuts in on the left as the announcer calls the name ("Warren!")
    ~0.85 VS slams into the seam on the word "Versus!", out of a rising swell, with a deep hit and a shock ring
    ~1.45 the answerer's card cuts in on the right on their name ("Stumpf!")
    ~2.05 FIGHT! (called), then the clip, which opens on its own line; each card plays the person's own video
    ground  two lit stages, red and navy, each under its own slow light rays with haze drifting up, split by a hot
            seam that flares on every hit; film grain so the dark never bands

The announcer (vsannounce.py, 10.28: Andrew on the narrator voice, "I hate the voice"; then from four new ones, "The
ring sounds the best ... I want the ring"): one shouted call, "Warren! Versus! Stumpf! FIGHT!", in our own voice, the
beats on its words (plan_call); without it (not made in time, the night shift's last minutes, tests) the card plays
its own sound alone. On FIGHT! a boxing bell rings, ding-ding-ding (Mix.bell; Andrew: "when fight comes, I want to
hear like a boxing bell")

All our own: Helvetica Neue lettering, the announcer cloned from our own Kokoro reading, the sound made here, the
faces and video from the clip's own master. Nothing from Capcom or NetherRealm (no logo, font, announcer, music),
never "FINISH HIM" or "FATALITY" (Warner Bros marks).

    who       the clip's own name plates (the engine's <stem>.cards.json in its job): two different people, the one
              asking on the left, the one answering on the right
    faces     what the engine saves with the clip (<clip>.faces/<who>.png and .mp4, from the MASTER, matched to the
              right person, the video never across a camera cut); a fighter faces the seam (mirrored when needed)
    when      at collect, after the engine's checks, like the pre-hook (prehook.py), and only when the account's
              switch is on: `factory.py social on <account> vs_card`
    files     the clip and each platform cut whose ceiling has room for it; a file without room posts as delivered
    kept      the delivered files in <draft>/.no-vs/, and the card itself there for a cut that arrives later

A clip with a VS card sits out of the pre-hook test (learn._cannot_apply): one opener, never two.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import board as B
from . import config as C
from . import prehook as PH
from . import switches as SW
from . import vsannounce as VA

JOBS = Path.home() / "Desktop" / "Clipping" / "Jobs"
KEEP = ".no-vs"
ANNOUNCER = True                                  # D RING (vsannounce.py); False plays the card's own sound alone
CARD_NAME = "vs-card.mp4"
HN = "/System/Library/Fonts/HelveticaNeue.ttc"
BLACK, BOLD, MED = 9, 4, 10                       # Helvetica Neue: Condensed Black, Condensed Bold, Medium
W, H, FPS, SR = 1080, 1920, 30, 48000
SECONDS = 3.2                                     # the most a card runs (the room every file must have)
MERGE, OPEN = 0.28, 330                           # the two grounds meet on the seam at 0.28 s, from 330 px apart
INK, WHITE = (8, 6, 6), (255, 255, 255)
FIRE = ((255, 242, 90), (255, 150, 0), (215, 20, 10))
METAL = ((255, 246, 214), (226, 172, 72), (112, 62, 18))
RED, BLUE = (230, 30, 30), (40, 110, 255)
ASKERS = {"questioner", "member", "chair", "senator", "representative", "interviewer", "host", "asker", "reporter"}


# the two cards share one slanted seam (it leans right going up); faces sit on each card's outer side so VS, at the
# shoulders, never covers one (v3's lesson)
def _seam(y: float) -> float:
    return 586 - (y - 440) * 0.10


L_TOP, L_BOT, R_TOP, R_BOT, GAP, VS_Y = 440, 1350, 530, 1440, 14, 1150
LEFT_POLY = [(40, L_TOP), (_seam(L_TOP) - GAP, L_TOP), (_seam(L_BOT) - GAP, L_BOT), (40, L_BOT)]
RIGHT_POLY = [(_seam(R_TOP) + GAP, R_TOP), (1040, R_TOP), (1040, R_BOT), (_seam(R_BOT) + GAP, R_BOT)]
SIZE_L = (int(_seam(L_TOP) - GAP - 40), L_BOT - L_TOP)
SIZE_R = (int(1040 - (_seam(R_BOT) + GAP)), R_BOT - R_TOP)


def on(acct) -> bool:
    return SW.mode(acct, "vs_card") == "on"


# ------------------------------------------------------------------------------------------------------ who fights
def _draft(slot: dict) -> Optional[Path]:
    p = (slot.get("clip") or {}).get("path")
    return Path(p).parent if p else None


def slot_job(slot: dict, acct=None) -> str:
    """The clip's job: from Publish/schedule.json by the slot's day and slug (bench._sched_job), else from the draft's
    cast-check.json. That file is written only while a cast still waits for Andrew, so a confirmed two-person fight,
    the very clip the card is for, never has it (10-27 and 10-28: 30 clips, 0 cards; the premium build chat's audit)."""
    if acct is not None:
        from . import bench as BN
        job = BN._sched_job(BN._sched(acct), str(slot.get("slug") or ""), str(slot.get("publish_at") or "")[:10])
        if job:
            return job
    d = _draft(slot)
    try:
        return str(json.loads((d / "cast-check.json").read_text()).get("job") or "") if d else ""
    except (OSError, ValueError):
        return ""


def job_cards(slot: dict, acct=None) -> Tuple[Optional[dict], str]:
    """The engine's placed plates for this clip ({"cards": [...]}), from its job (slot_job)."""
    if _draft(slot) is None:
        return None, "the slot holds no clip"
    job = slot_job(slot, acct)
    if not job:
        return None, "the clip's job is not known (not in Publish/schedule.json, no cast-check.json beside it)"
    stem = Path(slot["clip"]["path"]).stem
    p = JOBS / job / "clips" / f"{stem}.cards.json"
    if not job or not p.is_file():
        return None, f"no plates file for {stem} in the job {job or '?'}"
    try:
        return json.loads(p.read_text()), ""
    except ValueError:
        return None, f"{p.name} is not readable"


SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "phd", "md", "esq"}
PARTICLES = {"van", "von", "de", "del", "della", "der", "den", "di", "da", "du", "la", "le", "st", "saint", "ten", "ter"}
TWO_WORD = {"wasserman schultz", "blunt rochester", "cortez masto", "lujan grisham", "luján grisham"}


def _words(name: str) -> List[str]:
    """The name's words without a Jr., Sr. or III at the end."""
    ws = name.replace(",", " ").split()
    while len(ws) > 1 and re.sub(r"[^a-z]", "", ws[-1].lower()) in SUFFIXES:
        ws.pop()
    return ws


def surname(name: str) -> str:
    """The name the card shouts (Andrew 10.28: "Van Hollen" came out HOLLEN): the last word, with the particle before
    it after a first name ("Chris Van Hollen" is Van Hollen, "Van Taylor" is Taylor), a two-word surname whole
    ("Wasserman Schultz"), and never a Jr., Sr. or III ("Robert F. Kennedy Jr." is Kennedy)."""
    ws = _words(name)
    if len(ws) >= 2 and " ".join(ws[-2:]).lower() in TWO_WORD:
        return " ".join(ws[-2:])
    if len(ws) >= 3 and ws[-2].lower().rstrip(".") in PARTICLES:
        return " ".join(ws[-2:])
    return ws[-1] if ws else ""


def full_name(name: str) -> str:
    """First and last name for the announcer's second try ("Josh Hawley", "Robert Kennedy")."""
    ws, sur = _words(name), surname(name)
    return " ".join(([ws[0]] if len(ws) > len(sur.split()) else []) + [sur])


def announce(left: dict, right: dict) -> Tuple[Optional[dict], str]:
    """The announcer's call: the last names ("Warren! Versus! Stumpf!"); when it makes takes but none says them, first
    and last names ("Elizabeth Warren! Versus! John Stumpf!"). A last name on H goes straight to first and last names:
    the voice drops an H that opens a phrase ("Orly" for Hawley in every take, "Exeth" for Hegseth, and whisper's 60 %
    still passed Exeth), and after a first name it said "Hawley" in every take (10.29)."""
    last = (left["surname"].title(), right["surname"].title())
    full = (full_name(left.get("name") or last[0]), full_name(right.get("name") or last[1]))
    if full != last and any(n[:1] == "H" for n in last):
        return VA.call(*full)
    call, why = VA.call(*last)
    if call is None and why.startswith("none of ") and full != last:
        call, again = VA.call(*full)
        why = "" if call else f"{why}; then {again}"
    return call, why


def fighters(cards: dict) -> Tuple[List[dict], str]:
    """The two people the clip names (its placed plates, then anyone its slate names whose plate the render left out),
    the asker first (left, red), the answerer second (right, blue)."""
    seen: Dict[str, dict] = {}
    for c in list(cards.get("cards") or []) + list(cards.get("people") or []):
        who = str(c.get("who") or c.get("name") or "").strip().lower()
        if who and who not in seen and c.get("name"):
            seen[who] = c
    people = list(seen.values())
    if len(people) < 2:
        return [], "one person is named in the clip: a VS card needs two"
    askers = [c for c in people if str(c.get("role") or "").lower() in ASKERS]
    others = [c for c in people if c not in askers]
    pair = (askers[:1] + others[:1]) if askers and others else people[:2]
    out = []
    for c in pair:
        name = " ".join(str(c["name"]).split())
        out.append({"who": str(c.get("who") or "").lower(), "name": name, "surname": surname(name).upper(),
                    "title": " ".join(str(c.get("title") or "").split()).upper()})
    return out, ""


# ------------------------------------------------------------------------------------------------------ faces
def _mirror(rec: dict, outer: str) -> bool:
    """Turn the fighter toward the seam: the left card looks right, the right card looks left."""
    looks = str(rec.get("looks") or "")
    return (outer == "left" and looks == "left") or (outer == "right" and looks == "right")


def portrait(rec: dict, size: Tuple[int, int], outer: str):
    """The fighter's clean head-and-shoulders still (saved by the engine from the master: <clip>.faces/<who>.png), at
    the card's size, turned toward the seam."""
    from PIL import Image, ImageOps
    cw, ch = size
    im = Image.open(rec["png"]).convert("RGB")
    k = max(cw / im.width, ch / im.height)
    im = im.resize((max(cw, round(im.width * k)), max(ch, round(im.height * k))), Image.LANCZOS)
    x0 = (im.width - cw) // 2
    im = im.crop((x0, 0, x0 + cw, ch))                       # the face sits near the top: keep the top
    return ImageOps.mirror(im) if _mirror(rec, outer) else im


def moving(rec: dict, size: Tuple[int, int], outer: str) -> list:
    """The fighter's own video (<clip>.faces/<who>.mp4: the master round the face, no camera cut in it) as frames at
    the card's size, turned like the still; [] when the engine saved none."""
    from PIL import Image
    mp4 = rec.get("mp4")
    if not mp4 or not Path(mp4).is_file():
        return []
    cw, ch = size
    vf = (f"scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch}:(iw-{cw})/2:0"
          + (",hflip" if _mirror(rec, outer) else ""))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", mp4, "-vf", vf, "-r", str(FPS), "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=120).stdout
    n = len(raw) // (cw * ch * 3)
    return [Image.frombuffer("RGB", (cw, ch), raw[i * cw * ch * 3:(i + 1) * cw * ch * 3]) for i in range(n)]


# ------------------------------------------------------------------------------------------------------ the sound
class Mix:
    """A stereo bus at SR with a shared room (a convolution reverb) and the card's own sound effects."""

    def __init__(self, seconds: float):
        import numpy as np
        self.np = np
        self.n = int(round(seconds * SR))
        self.dry = np.zeros((self.n, 2), np.float32)
        self.wet = np.zeros((self.n, 2), np.float32)
        self.rng = np.random.default_rng(7)

    def add(self, sig, at: float, gain: float = 1.0, pan: float = 0.0, send: float = 0.2) -> None:
        np = self.np
        sig = np.asarray(sig, np.float32)
        if sig.ndim == 1:
            left, right = math.cos((pan + 1) * math.pi / 4), math.sin((pan + 1) * math.pi / 4)
            sig = np.stack([sig * left * 1.41, sig * right * 1.41], axis=1)
        s0 = int(round(at * SR))
        if s0 >= self.n:
            return
        seg = sig[: self.n - max(0, s0)] * gain
        if s0 < 0:
            seg = seg[-s0:]
            s0 = 0
        self.dry[s0:s0 + len(seg)] += seg
        self.wet[s0:s0 + len(seg)] += seg * send

    def _filtered(self, n: int, lo: float, hi: float, seed: int):
        """Noise band-limited to lo..hi Hz (by FFT)."""
        np = self.np
        x = np.random.default_rng(seed).standard_normal(n)
        f = np.fft.rfftfreq(n, 1 / SR)
        X = np.fft.rfft(x) * ((f >= lo) & (f <= hi))
        y = np.fft.irfft(X, n)
        return (y / (np.abs(y).max() + 1e-9)).astype(np.float32)

    def whoosh(self, at: float, dur: float, pan_from: float, pan_to: float, gain: float = 0.32) -> None:
        """Air past the lens: noise in a band sweeping up, panned across, as a card cuts in."""
        np = self.np
        n = int(dur * SR)
        hop, win = 480, 1920
        out = np.zeros((n + win, 2), np.float32)
        hann = np.hanning(win).astype(np.float32)
        for k, s in enumerate(range(0, n, hop)):
            u = s / n
            c = 350 * (8 ** u)                              # 350 Hz to 2.8 kHz
            g = self._filtered(win, c * 0.6, c * 1.6, 100 + k) * hann * math.sin(math.pi * u) ** 1.4
            p = pan_from + (pan_to - pan_from) * u
            out[s:s + win, 0] += g * math.cos((p + 1) * math.pi / 4)
            out[s:s + win, 1] += g * math.sin((p + 1) * math.pi / 4)
        self.add(out[:n], at, gain, send=0.15)

    def swell(self, end: float, dur: float = 0.38, gain: float = 0.30) -> None:
        """A rising swell into a hit (the room breathing in)."""
        np = self.np
        n = int(dur * SR)
        t = np.linspace(0, 1, n, dtype=np.float32)
        body = self._filtered(n, 120, 4000, 21) * t ** 2.4
        self.add(np.stack([body, self._filtered(n, 120, 4000, 22) * t ** 2.4], 1), end - dur, gain, send=0.3)

    def hit(self, at: float, gain: float = 1.0, bright: bool = False) -> None:
        """The impact: a sub drop under a punch of low noise, a crack on top, a short inharmonic ring."""
        np = self.np
        n = int(1.1 * SR)
        t = np.arange(n, dtype=np.float32) / SR
        f = 34 + 95 * np.exp(-t * 16)
        sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.2) * (1 - np.exp(-t * 900))
        body = self._filtered(n, 30, 700, 31) * np.exp(-t * 26)
        crack = self._filtered(n, 2500, 12000, 32) * np.exp(-t * (110 if bright else 150))
        ring = sum(np.sin(2 * np.pi * hz * t + k) for k, hz in enumerate((196, 293, 415, 587, 739))) / 5
        ring = ring * np.exp(-t * 7) * (0.16 if bright else 0.10)
        mono = 0.95 * sub + 0.55 * body + (0.45 if bright else 0.32) * crack + ring
        side = 0.55 * self._filtered(n, 30, 700, 33) * np.exp(-t * 26) + 0.3 * self._filtered(n, 2500, 12000, 34) \
            * np.exp(-t * 150)
        self.add(np.stack([mono + 0.25 * side, mono - 0.25 * side], 1), at, gain, send=0.28)

    def bell(self, at: float, strikes: int = 3, gap: float = 0.15, gain: float = 0.62) -> None:
        """A boxing ring's bell, struck `strikes` times `gap` apart: a bright brass bell made here (inharmonic partials
        ringing out, the high ones dying first, two near the prime beating), each strike a hammer click."""
        np = self.np
        n = int(1.6 * SR)
        t = np.arange(n, dtype=np.float32) / SR
        f0 = 1180.0
        partials = ((0.5, 0.25, 2.0), (1.0, 1.0, 3.2), (1.003, 0.6, 3.0), (1.19, 0.35, 4.5), (1.56, 0.45, 5.5),
                    (2.0, 0.5, 6.5), (2.51, 0.3, 8.0), (2.66, 0.25, 9.0), (3.01, 0.2, 11.0), (4.07, 0.12, 14.0))
        ring = sum(a * np.sin(2 * np.pi * f0 * r * t + k) * np.exp(-t * d) for k, (r, a, d) in enumerate(partials))
        ring = ring / (np.abs(ring).max() + 1e-9)
        click = self._filtered(n, 2500, 9000, 51) * np.exp(-t * 400) * 0.6
        one = ((ring + click) * (1 - np.exp(-t * 2000))).astype(np.float32)
        for k in range(strikes):
            self.add(one * (0.85 ** k), at + k * gap, gain, send=0.3)

    def room(self) -> None:
        """A bed under the whole card: a low hum that keeps it from sounding like effects in a void."""
        np = self.np
        t = np.arange(self.n, dtype=np.float32) / SR
        hum = (np.sin(2 * np.pi * 55 * t) + 0.5 * np.sin(2 * np.pi * 82.4 * t)) * (0.6 + 0.4 * np.sin(2 * np.pi * 3
                                                                                                     * t))
        fade = np.minimum(1, t / 0.15) * np.minimum(1, (t[-1] - t) / 0.12)
        self.add(hum * fade, 0.0, 0.045, send=0.0)

    def master(self):
        """Dry + the room (a 1.1 s decaying noise impulse, convolved), peak -1 dB."""
        np = self.np
        m = int(1.1 * SR)
        t = np.arange(m, dtype=np.float32) / SR
        out = self.dry.copy()
        for ch in (0, 1):
            ir = self._filtered(m, 80, 7000, 41 + ch) * np.exp(-t * 5.5)
            ir[: int(0.012 * SR)] = 0                          # a short pre-delay
            L = self.n + m
            nfft = 1 << (L - 1).bit_length()
            wet = np.fft.irfft(np.fft.rfft(self.wet[:, ch], nfft) * np.fft.rfft(ir, nfft), nfft)[: self.n]
            out[:, ch] += 0.06 * wet.astype(np.float32)
        return (out / (np.abs(out).max() + 1e-9) * 0.89).astype(np.float32)


# ------------------------------------------------------------------------------------------------------ the picture
def _font(face: int, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(HN, size, index=face)


def _ease(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def _ease_in(t: float) -> float:
    """Faster and faster into the hit."""
    t = min(max(t, 0.0), 1.0)
    return t ** 2.2


def _hot(s: str, size: int, grad=FIRE, lean: float = 0.16, rim=None):
    """Arcade lettering on its own layer: a vertical gradient, a thick black outline (a thin coloured rim inside it
    if asked), a hard dark shadow, leaned forward."""
    import numpy as np
    from PIL import Image, ImageDraw
    f = _font(BLACK, size)
    stroke, shadow = max(4, size // 20), max(4, size // 24)
    l, t, r, b = f.getbbox(s, stroke_width=stroke)
    pad = stroke + shadow + 6
    w, h = r - l + 2 * pad + int(lean * (b - t)), b - t + 2 * pad
    o = (pad - l + int(lean * (b - t) / 2), pad - t)

    def m(sw, dx=0, dy=0):
        im = Image.new("L", (w, h), 0)
        ImageDraw.Draw(im).text((o[0] + dx, o[1] + dy), s, font=f, fill=255, stroke_width=sw, stroke_fill=255)
        return im
    y = np.linspace(0, 1, h)[:, None, None]
    c0, c1, c2 = (np.array(c, np.float32) for c in grad)
    g = np.where(y < 0.5, c0 + (c1 - c0) * (y / 0.5), c1 + (c2 - c1) * ((y - 0.5) / 0.5))
    fill = Image.fromarray(np.ascontiguousarray(np.broadcast_to(g, (h, w, 3))).astype(np.uint8))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.paste((60, 0, 0, 255), (0, 0), m(stroke, shadow, shadow))
    layer.paste(INK + (255,), (0, 0), m(stroke))
    if rim:
        layer.paste(rim + (255,), (0, 0), m(max(2, stroke // 3)))
    layer.paste(fill, (0, 0), m(0))
    return layer.transform((w, h), Image.AFFINE, (1, lean, -lean * h / 2, 0, 1, 0), Image.BICUBIC)


def _fit_hot(s: str, size: int, room: int, grad=METAL):
    while size > 60:
        lay = _hot(s, size, grad)
        if lay.width <= room:
            return lay
        size -= 6
    return _hot(s, size, grad)


def _put(c, layer, x, y, anchor="mm", alpha=1.0):
    w, h = layer.size
    if alpha < 1:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    x0 = x - w // 2 if anchor[0] == "m" else x - w if anchor[0] == "r" else x
    c.paste(layer, (round(x0), round(y - h // 2)), layer)


def _outlined(d, xy, s, f, fill, anchor="la", sw=4):
    d.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=sw, stroke_fill=INK)


def _fit_text(d, s: str, face: int, size: int, room: int):
    f = _font(face, size)
    while size > 20 and d.textlength(s, font=f) > room:
        size -= 2
        f = _font(face, size)
    return f


class Ground:
    """The arena behind the cards, made fresh every frame (v5, Andrew on v4's flat split: "The background still looks
    kind of shitty and low quality. Upgrade it"): the red and the navy slide in from the two sides, each with a hot
    leading edge, and meet on the seam with a flash; then two lit stages, each under its own light (soft rays turning
    slowly, haze drifting up through it), split by a hot seam that flares on every hit (the meeting, VS, FIGHT!).
    Drawn at half size and scaled up (light is smooth), then film grain at full size, so the dark never bands into
    steps the way v4's flat gradient did."""

    TALL = 260                                         # rows of extra haze, so it can drift up

    def __init__(self, beats: dict):
        import numpy as np
        from PIL import Image
        self.np, self.b = np, beats
        w, h = W // 2, H // 2
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        self.X, self.Y = xx * 2, yy * 2                # full-size coordinates
        self.seam = _seam(self.Y)
        rng = np.random.default_rng(5)
        tall = h + self.TALL
        haze = np.zeros((tall, w), np.float32)
        for cells, amp in ((5, 1.0), (10, 0.55), (20, 0.3), (40, 0.15)):
            small = rng.random((max(2, round(cells * tall / w)), cells)).astype(np.float32)
            haze += amp * np.asarray(Image.fromarray(small, "F").resize((w, tall), Image.BICUBIC))
        self.haze = 0.64 + 0.66 * (haze - haze.min()) / (haze.max() - haze.min() + 1e-9)
        r = np.hypot((self.X - 540) / 620, (self.Y - 900) / 1050)
        self.vig = np.clip(1 - 0.5 * r ** 2, 0.25, 1)[..., None]
        self.grain = [rng.normal(0, 2.4, (H, W, 1)).astype(np.float32) for _ in range(5)]

    def _stage(self, cx: float, dark, lit, t: float, turn: float):
        """One side's colour under its own stage light, hung above the frame over x = cx: brightest at the top and
        over its card, falling off down the frame, with soft beams coming down through the haze and swaying slowly
        (a light far above, so the beams fan gently and never meet in a star the way v5's first try did)."""
        np = self.np
        a = np.arctan2(self.Y + 650, self.X - cx)
        beams = (0.5 * np.sin(23 * a + 0.6 * t * turn) + 0.3 * np.sin(41 * a - 0.9 * t * turn + 1.3)
                 + 0.2 * np.sin(67 * a + 1.4 * t * turn + 2.1))
        light = np.exp(-((self.X - cx) / 640) ** 2) * (0.42 + 0.58 * np.exp(-(self.Y / 1500) ** 2))
        light = light * (1 + 0.26 * beams * np.exp(-self.Y / 1700))
        dark, lit = np.asarray(dark, np.float32), np.asarray(lit, np.float32)
        return dark + (lit - dark) * light[..., None]

    def flare(self, i: int) -> float:
        """How hard the seam burns at frame i: a flash at each hit, dying in a few frames."""
        b = self.b
        return sum(a * math.exp(-(i - b[k]) / 3.2) for k, a in (("merge", 2.6), ("vs", 1.6), ("fight", 1.6))
                   if i >= b[k])

    def frame(self, i: int):
        np = self.np
        from PIL import Image
        b, t = self.b, i / FPS
        off = (1 - _ease_in(i / max(1, b["merge"]))) * OPEN
        rows = self.X.shape[0]
        k = min(self.TALL - 1, i * 2)                  # the haze drifts up 4 px a frame
        hz = self.haze[k:k + rows][..., None]
        red = self._stage(240 - off, (12, 2, 4), (158, 16, 22), t, 1.0)
        navy = self._stage(840 + off, (2, 4, 14), (16, 42, 160), t, -1.0)
        mr = np.clip((self.seam - off - self.X) / 3 + 0.5, 0, 1)[..., None]
        mb = np.clip((self.X - self.seam - off) / 3 + 0.5, 0, 1)[..., None]
        img = (np.asarray((5, 4, 6), np.float32) * (1 - mr - mb) + red * mr + navy * mb) * hz * self.vig
        if i < b["merge"]:                             # the two leading edges, hot, closing in
            er = np.exp(-((self.X - (self.seam - off)) / 9) ** 2)[..., None]
            eb = np.exp(-((self.X - (self.seam + off)) / 9) ** 2)[..., None]
            img += er * np.asarray((255, 120, 40), np.float32) * 0.85 + eb * np.asarray((80, 160, 255),
                                                                                        np.float32) * 0.85
        else:                                          # one seam, burning; it flares on every hit
            fl = self.flare(i)
            ds = self.X - self.seam
            core = np.exp(-(ds / 2.6) ** 2)[..., None] * (0.92 + 0.08 * math.sin(i * 1.7))
            halo = np.exp(-(ds / 30) ** 2)[..., None] * (0.28 + 0.25 * fl)
            bloom = np.exp(-(ds / 170) ** 2)[..., None] * (0.22 * fl)
            img += (core * np.asarray((255, 240, 210), np.float32) + halo * np.asarray((255, 160, 80), np.float32)
                    + bloom * np.asarray((255, 190, 140), np.float32))
        half = Image.fromarray(img.clip(0, 255).astype(np.uint8))
        full = np.asarray(half.resize((W, H), Image.BICUBIC)).astype(np.float32) + self.grain[i % len(self.grain)]
        return Image.fromarray(full.clip(0, 255).astype(np.uint8)).convert("RGBA")


class Card:
    """Every frame of one VS card. `beats` are the frames each thing lands on: the grounds meeting, the left card,
    VS, the right card, FIGHT!"""

    def __init__(self, left: dict, right: dict, line: str, beats: dict, part: Optional[int] = None):
        from PIL import Image, ImageDraw, ImageFilter
        self.left, self.right, self.line, self.b = left, right, line, beats
        # THE SEQUEL (2026-10-29; Andrew 10.28: "a sequel's VS card says PART 2 / PART 3 big across the top", so the
        # cover shows it too): in the card's own lettering, up from the grounds' merge to the last frame
        self.part = int(part) if part and int(part) >= 2 else None
        self.ground = Ground(beats)
        self.mask_l = self._mask(LEFT_POLY)
        self.mask_r = self._mask(RIGHT_POLY)
        self.shadow_l, self.shadow_r = (self._shadow(m, ImageFilter) for m in (self.mask_l, self.mask_r))
        lw, rw = SIZE_L[0], SIZE_R[0]
        self.lname = _fit_hot(left["surname"], 136, lw - 20)
        self.rname = _fit_hot(right["surname"], 136, rw - 20)
        self.vs = _hot("VS", 300, FIRE, rim=(255, 220, 120))
        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        self.lsub = _fit_text(probe, left["title"], BOLD, 34, lw - 20) if left["title"] else None
        self.rsub = _fit_text(probe, right["title"], BOLD, 34, rw - 20) if right["title"] else None
        self.lx = int(40 + lw / 2)
        self.rx = int(_seam(R_BOT) + GAP + rw / 2)

    @staticmethod
    def _mask(poly):
        from PIL import Image, ImageDraw
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).polygon(poly, fill=255)
        return m

    @staticmethod
    def _shadow(mask, ImageFilter):
        """A soft shadow under a card, down and to the right, so the card sits on the stage instead of on top of it."""
        from PIL import Image
        s = Image.new("RGBA", (W, H), INK + (0,))
        s.putalpha(mask.filter(ImageFilter.GaussianBlur(20)).point(lambda v: int(v * 0.7)))
        return s

    @staticmethod
    def _face(img, team, poly, mask):
        """One frame of a fighter's card: the picture graded, its bottom fading into the ground, the team's rim."""
        import numpy as np
        from PIL import Image, ImageDraw, ImageEnhance
        x0, y0 = min(p[0] for p in poly), min(p[1] for p in poly)
        h = img.height
        im = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.25)).enhance(1.10)
        a = np.asarray(im.convert("RGB")).astype(np.float32)
        v = np.linspace(0, 1, h)[:, None, None]
        a = a * (1 - np.clip((v - 0.58) / 0.28, 0, 1) * 0.97)          # deep enough to lose the desk name plates
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.paste(Image.fromarray(a.clip(0, 255).astype(np.uint8)), (round(x0), round(y0)))
        layer.putalpha(mask)
        d = ImageDraw.Draw(layer)
        d.line(poly + [poly[0]], fill=team + (255,), width=9, joint="curve")
        d.line(poly + [poly[0]], fill=INK + (255,), width=3, joint="curve")
        return layer

    def _frame_of(self, f: dict, k: int):
        frames = f.get("frames") or []
        return frames[min(max(0, k), len(frames) - 1)] if frames else f["img"]

    def frame(self, i: int):
        from PIL import Image, ImageDraw
        b = self.b
        c = self.ground.frame(i)
        dim = 0.0 if i < b["fight"] else 0.5
        for f, team, poly, mask, shadow, at, dx0 in (
                (self.left, RED, LEFT_POLY, self.mask_l, self.shadow_l, b["left"], -720),
                (self.right, BLUE, RIGHT_POLY, self.mask_r, self.shadow_r, b["right"], 720)):
            if i < at:
                continue
            a = _ease((i - at) / 6)
            layer = self._face(self._frame_of(f, i - at), team, poly, mask)
            push = 0.5 * max(0, i - max(at, b["vs"]))
            dx = dx0 * (1 - a) + (push if dx0 < 0 else -push)
            c.alpha_composite(shadow.transform((W, H), Image.AFFINE, (1, 0, -(dx + 14), 0, 1, -22)))
            layer = layer.transform((W, H), Image.AFFINE, (1, 0, -dx, 0, 1, 0))
            if dim:
                layer = Image.composite(Image.blend(layer, Image.new("RGBA", (W, H), INK + (255,)), dim), layer,
                                        layer.getchannel("A"))
            c.alpha_composite(layer)
        d = ImageDraw.Draw(c)
        if i < b["fight"]:
            for f, name, sub, x, y, at in ((self.left, self.lname, self.lsub, self.lx, L_BOT + 2, b["left"] + 3),
                                           (self.right, self.rname, self.rsub, self.rx, R_BOT + 6, b["right"] + 3)):
                if i < at:
                    continue
                k = _ease((i - at) / 6)
                _put(c, name, x, y + round(60 * (1 - k)), alpha=k)
                if sub is not None and k > 0.6:
                    _outlined(d, (x, y + 78), f["title"], sub, WHITE, anchor="mm", sw=3)
        if b["vs"] - 4 <= i < b["fight"]:
            k = (i - (b["vs"] - 4)) / 4
            s = 2.8 - 1.8 * _ease(k) if i < b["vs"] else (0.86 if i == b["vs"] else 0.95 if i == b["vs"] + 1 else 1.0)
            s *= 1 + 0.025 * max(0, i - b["vs"] - 2) / 15
            _put(c, self.vs if s == 1.0 else _hot("VS", round(300 * s), FIRE, rim=(255, 220, 120)), 548, VS_Y)
        if b["vs"] <= i < b["vs"] + 5:                        # a shock ring off the hit, glowing, thinning as it spreads
            from PIL import ImageFilter
            k = i - b["vs"]
            ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            rx = 180 + 120 * k
            ImageDraw.Draw(ring).ellipse([548 - rx, VS_Y - rx * 0.8, 548 + rx, VS_Y + rx * 0.8],
                                         outline=(255, 226, 150, int(255 * (1 - k / 5))), width=max(3, 18 - 4 * k))
            c.alpha_composite(ring.filter(ImageFilter.GaussianBlur(10)))
            c.alpha_composite(ring)
        if i >= b["fight"]:
            s = 1.35 - 0.35 * _ease((i - b["fight"]) / 3)
            _put(c, _hot("FIGHT!", round(320 * s), FIRE, rim=(255, 220, 120)), 540, 980)
        if self.part and i >= b["merge"]:
            s = 1.3 - 0.3 * _ease((i - b["merge"]) / 3)
            _put(c, _hot(f"PART {self.part}", round(PART_PX * s), FIRE, rim=(255, 220, 120)), 540, PART_Y)
        _outlined(d, (540, 1850), self.line, _font(BOLD, 28), (230, 220, 200), anchor="mm", sw=3)
        c = c.convert("RGB")
        shake = {b["merge"]: (9, -6), b["merge"] + 1: (-5, 4), b["vs"]: (16, -10), b["vs"] + 1: (-11, 8),
                 b["vs"] + 2: (5, -3), b["right"] + 5: (6, 4), b["fight"]: (18, 11), b["fight"] + 1: (-9, -6)}
        if i in shake:
            dx, dy = shake[i]
            c = c.transform((W, H), Image.AFFINE, (1, 0, -dx, 0, 1, -dy), fillcolor=INK)
        return c


CALL_AT = MERGE + 0.02                            # the announcer's call starts as the grounds meet
PART_PX, PART_Y = 170, 250                         # THE SEQUEL's PART 2 across the top


def plan(voices: list) -> Tuple[dict, float]:
    """When each beat lands (seconds) for a card with no announcer (voices all None: the defaults), or with one sound
    per word: the grounds meet first, the left card on its name, VS on the first syllable of "Versus!", the right
    card as it ends, FIGHT! after the right name. At most SECONDS."""
    d = [(len(v) / SR if v is not None else dflt) for v, dflt in zip(voices, (0.55, 0.6, 0.55, 0.5))]
    left = MERGE
    vs = max(left + 0.45, left + 0.02 + d[0] - 0.05)       # the voice says "Versus!" from vs - 0.03
    right = max(vs + 0.35, vs - 0.03 + d[1] - 0.06)
    fight = right + 0.02 + d[2] + 0.06
    end = min(SECONDS, max(fight + 0.7, fight - 0.05 + d[3] + 0.10))         # the bell rings a little
    return {"merge": MERGE, "left": left, "vs": vs, "right": right, "fight": fight}, end


def plan_call(call: dict) -> Tuple[dict, float]:
    """The beats on the announcer's own words (vsannounce.call: where each word starts in the line, which starts at
    CALL_AT): the left card as the left name starts, VS just after "Versus" starts, the right card on the right name,
    FIGHT! just after "Fight" starts, the end once the call has rung out. At most SECONDS."""
    s = {w: CALL_AT + v for w, v in call["starts"].items()}
    vs = max(MERGE + 0.3, s["versus"] + 0.03)
    right = max(vs + 0.3, s["right"] - 0.02)
    fight = max(right + 0.35, s["fight"] + 0.03)
    end = min(SECONDS, max(fight + 0.7, CALL_AT + float(call["speech"]) + 0.2))   # the bell rings a little
    return {"merge": MERGE, "left": MERGE, "vs": vs, "right": right, "fight": fight}, end


def full_at(beats: dict) -> float:
    """The second where the whole card is in: both fighters, both names and titles, VS at rest, before FIGHT! takes
    its place (Andrew 10.30, on a fight clip's cover: "that would be the thumbnail"). `beats` in frames; two frames
    before FIGHT! when the right card has landed by then (its 6 frames), at the middle of the frame, so a player's
    cover and ffmpeg's frame both land on the whole card."""
    i = min(max(beats["right"] + 6, beats["fight"] - 2), beats["fight"] - 1)
    return round((i + 0.5) / FPS, 3)


def make(left: dict, right: dict, line: str, out: Path, info: Optional[dict] = None,
         part: Optional[int] = None) -> Path:
    """Render the card (1080x1920, 30 fps, its own sound, at most SECONDS) to `out`; `info` gets "announcer": how the
    call was made ("made", "kept") or why the card plays its sound alone."""
    import numpy as np
    with tempfile.TemporaryDirectory() as td:
        call, why = (announce(left, right) if ANNOUNCER
                     else (None, "the announcer is switched off (vscard.ANNOUNCER)"))
        if info is not None:
            info["announcer"] = ("kept" if call.get("kept") else "made") if call else f"none: {why}"
        t, end = plan_call(call) if call else plan([None] * 4)
        beats = {k: int(round(v * FPS)) for k, v in t.items()}
        if info is not None:
            info["vs_at"] = full_at(beats)                       # a fight clip's cover (covers.vs_moment)
        n = int(round(end * FPS))
        mix = Mix(n / FPS)
        mix.room()
        mix.whoosh(0.0, t["merge"], -0.95, -0.1, 0.26)            # the two grounds closing in
        mix.whoosh(0.0, t["merge"], 0.95, 0.1, 0.26)
        mix.hit(t["merge"], 0.55)
        mix.whoosh(t["left"] - 0.02, 0.28, -0.9, -0.2)
        mix.swell(t["vs"])
        mix.hit(t["vs"], 0.78)
        mix.whoosh(t["right"] - 0.02, 0.28, 0.9, 0.2)
        mix.hit(t["fight"], 0.45)
        mix.bell(t["fight"])                                     # ding-ding-ding: the round starts
        if call:
            mix.add(call["signal"], CALL_AT, 1.15, send=0.12)     # the voice leads; the hits sit under it
        master = mix.master()
        k = min(len(master), int(0.08 * SR))
        master[-k:] *= np.linspace(1, 0, k, dtype=np.float32)[:, None]      # the echo never cuts off
        pcm = (master * 32767).astype("<i2")
        wav = Path(td) / "sound.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
        card = Card(left, right, line, beats, part=part)
        raw = b"".join(card.frame(i).tobytes() for i in range(n))
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(FPS), "-i", "-", "-i", str(wav), *PH.X264,
                            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", *PH.AAC, "-shortest", "-movflags", "+faststart",
                            str(out)], input=raw, capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {r.stderr.decode(errors='replace')[-300:]}")
    return out


def source_line(slot: dict) -> str:
    """The masthead's own words for the card's foot: POLITICS and the clip's credit from its job's slate."""
    d = _draft(slot)
    try:
        job = str(json.loads((d / "cast-check.json").read_text()).get("job") or "") if d else ""
        sl = json.loads((JOBS / job / "slate.json").read_text())
        c = next((x for x in sl.get("clips") or [] if x.get("slug") == slot.get("slug")), {})
        credit = str(c.get("credit") or (c.get("framing") or {}).get("credit") or "")
    except (OSError, ValueError, StopIteration):
        credit = ""
    credit = re.sub(r"\s+", " ", credit).strip().upper()
    return "POLITICS" + (f"  ·  {credit}" if credit else "")


# ------------------------------------------------------------------------------------------------------ the slot
def _keep(p: Path) -> None:
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


def card_part(slot: dict) -> Optional[int]:
    """THE SEQUEL's part for the card's top (2 or 3), from the slot's "sequel" (collect reads it off the slate)."""
    try:
        n = int((slot.get("sequel") or {}).get("part") or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    return n if n >= 2 else None


def build(slot: dict, acct=None) -> Tuple[Optional[Path], dict, str]:
    """(the card file in <draft>/.no-vs/, what is on it, '') or (None, {}, why not)."""
    cards, why = job_cards(slot, acct)
    if cards is None:
        return None, {}, why
    pair, why = fighters(cards)
    if not pair:
        return None, {}, why
    saved = cards.get("faces") or {}
    clip = Path(slot["clip"]["path"])
    keep = clip.parent / KEEP
    keep.mkdir(exist_ok=True)
    for f, size, outer in ((pair[0], SIZE_L, "left"), (pair[1], SIZE_R, "right")):
        rec = saved.get(str(f.get("who") or "").lower())
        if not rec or not Path(rec.get("png", "")).is_file():
            return None, {}, (f"no clean face of {f['name']} saved with the clip (the engine saves them from its "
                              f"cast's check frames since 2026-10-28)")
        f["img"] = portrait(rec, size, outer)
        f["frames"] = moving(rec, size, outer)
        f["master_at"] = rec.get("master_at")
    info: dict = {}
    part = card_part(slot)
    out = make(pair[0], pair[1], source_line(slot), keep / CARD_NAME, info, **({"part": part} if part else {}))
    if part:
        info["part"] = part
    return out, {"left": pair[0]["name"], "right": pair[1]["name"],
                 "faces_at": [pair[0]["master_at"], pair[1]["master_at"]],
                 "moving": [bool(pair[0]["frames"]), bool(pair[1]["frames"])], **info}, ""


def apply(cfg: C.Config, acct, slot: dict) -> Tuple[Optional[dict], str]:
    """At collect: (record, '') when the card went on, (None, why) when not. Off unless the account's switch is."""
    if not on(acct):
        return None, "the vs_card switch is off"
    files = PH.ceilings(acct, slot)
    if not files:
        return None, "the slot holds no clip"
    try:
        card, who, why = build(slot, acct)
    except Exception as e:  # noqa: BLE001  (a clip is never lost over its opener)
        card, who, why = None, {}, f"the card could not be made ({e})"[:300]
    if card is None:
        B.note(slot, f"no VS card: {why}")
        return None, why
    secs_card = PH._seconds(card) or SECONDS
    done, hows, skipped = [], [], {}
    try:
        for kind, p, secs, hi in files:
            _keep(p)
            _restore(p)
            if PH._delivered_secs(slot["clip"] if kind == "clip" else slot["cuts"][kind]) + secs_card > hi - 0.1:
                skipped[kind] = f"no room under its {hi:.0f}s ceiling"
                continue
            hows.append(PH.join(card, p))
            done.append((kind, p))
    except Exception as e:  # noqa: BLE001  (every file goes back as delivered)
        for _kind, p in done:
            _restore(p)
        B.note(slot, f"no VS card: it could not be joined ({e})"[:300])
        return None, f"the card could not be joined ({e})"[:300]
    if not done:
        B.note(slot, "no VS card: no file has room for it")
        return None, "no file has room for it"
    for kind, p in done:
        secs = PH._seconds(p)
        row = slot["clip"] if kind == "clip" else slot["cuts"][kind]
        row.setdefault("delivered_sha256", row.get("sha256"))         # its identity on the board (B.clip_idents)
        row.setdefault("delivered_seconds", row.get("seconds"))
        row.update(sha256=B.sha256_file(p), seconds=round(secs, 3) if secs else row.get("seconds"))
    rec = {**who, "seconds": round(secs_card, 2), "card": str(card), "files": [k for k, _ in done],
           "join": sorted(set(hows)), "skipped": skipped,
           "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    slot["vs_card"] = rec
    B.note(slot, f"VS card {who['left']} vs {who['right']} put in front of {', '.join(k for k, _ in done)}"
                 + (f" (not {', '.join(skipped)}: no room)" if skipped else ""))
    return rec, ""


def doctor(cfg: C.Config, now=None) -> List[Tuple[Optional[bool], str, str]]:
    """One line per owner account: the VS card's switch and what it waits for. Read only."""
    out: List[Tuple[Optional[bool], str, str]] = []
    for a in cfg.accounts.values():
        if SW._is_client(a):
            continue
        if on(a):
            out.append((None, f"{a.name} vs_card: on (a clip that names two people opens on its VS card)", ""))
        else:
            out.append((None, f"{a.name} vs_card: off; it waits for Andrew's word on the proofs (Desktop/CLIP "
                              f"FACTORY/Premium Build 10.28), then factory.py social on {a.slug} vs_card", ""))
    return out


def apply_cut(acct, slot: dict, plat: str) -> str:
    """A platform cut that arrived after its clip took the card gets the same card when it fits ('' when it went on)."""
    rec = slot.get("vs_card") or {}
    cut = (slot.get("cuts") or {}).get(plat) or {}
    if not rec or not cut.get("path"):
        return "no VS card on this slot"
    from . import outlets as O
    card = Path(rec.get("card") or "")
    secs_card = float(rec.get("seconds") or SECONDS)
    if not card.is_file():
        why = "the card file is gone"
    elif PH._delivered_secs(cut) + secs_card > O.band(acct, plat, cut=True)[1] - 0.1:
        why = f"it would take the {plat} cut past {O.band(acct, plat, cut=True)[1]:.0f}s"
    else:
        p = Path(cut["path"])
        try:
            _keep(p)
            _restore(p)
            PH.join(card, p)
        except Exception as e:  # noqa: BLE001
            _restore(p)
            why = f"it could not be joined ({e})"[:300]
        else:
            secs = PH._seconds(p)
            cut.setdefault("delivered_sha256", cut.get("sha256"))
            cut.setdefault("delivered_seconds", cut.get("seconds"))
            cut.update(sha256=B.sha256_file(p), seconds=round(secs, 3) if secs else cut.get("seconds"))
            rec.setdefault("files", []).append(plat)
            B.note(slot, f"VS card put in front of the late {plat} cut")
            return ""
    B.note(slot, f"no VS card on the {plat} cut: {why}")
    rec.setdefault("skipped", {})[plat] = why
    return why
