"""THE VS CARD's announcer (2026-10-28). Andrew on the first voice, the Politics's Kokoro narrator: "I hate the voice ...
We need to have a better voice ... closer to our Street Fighter. Or ... Mortal Kombat"; then, from the audition of four
(Desktop/CLIP FACTORY/Premium Build 10.28/Proofs/Voice audition 10.28): "Use voice C ARCADE ... Tighten it and wire
it in", then, hearing it again: "I actually like the ring ... The ring sounds the best ... I don't want arcade. I want
the ring" (and a boxing bell on FIGHT!: vscard.Mix.bell).

D RING: the whole call, "Hawley! Versus! Calhoun! FIGHT!", shouted in one breath by Chatterbox (Resemble AI, MIT,
~/.venvs/announcer, vsannounce_gen.py) in a voice cloned from our own Kokoro reading (render/announcer/ref-bm_george.wav,
a deep British voice), so the voice is ours and no game's. Of a few takes (the straight read, then the drawn one),
whisper must hear the call and the sound must hold its four words; only the silence between them is shortened (a short
breath, a beat before FIGHT!), then the ring: lower and fuller, a little growl, a long hall and one slap echo off the
far wall. The card's beats land on the words (vscard.plan_call).

Each pair is made once and kept (state/announcer/). Never made in the night shift's last minutes (QUIET), past its time
budget, in tests, or without its environment: the card then plays its own sound alone. A clip never waits on its voice.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from . import config as C

SR = 48000
PY = Path.home() / ".venvs" / "announcer" / "bin" / "python"
GEN = Path(__file__).with_name("vsannounce_gen.py")
REF = C.ROOT / "render" / "announcer" / "ref-bm_george.wav"
WHISPER = Path("/opt/homebrew/bin/whisper-cli")
WHISPER_MODEL = Path.home() / "tableflip-app" / "data" / "models" / "ggml-large-v3-turbo.bin"
STYLE = "ring-1"                   # part of every kept file's name: a new voice or arena gets a new one
EXAG, CFG, TEMP, TAKES = 1.2, 0.30, 0.80, 2   # TAKES of each read
PITCH, DRIVE, LOW, BITE, HALL, HALL_S = 0.88, 1.8, 0.7, 0.6, 0.36, 2.0
ECHO = ((0.20, 0.28),)              # (delay s, gain): the slap off the arena's far wall
GAPS = (0.05, 0.07, 0.12)           # the silence after each of the first three words: the last is the beat before FIGHT!
QUIET = ("05:40", "06:20")          # the night shift's last minutes: no model then
BUDGET = 240                        # seconds for the model, every take
WORDS = ("left", "versus", "right", "fight")


def cache_dir() -> Path:
    return C.STATE / "announcer"


def _key(left: str, right: str) -> str:
    def k(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return f"{STYLE}--{k(left)}--{k(right)}"


def _quiet(now: datetime) -> bool:
    hm = now.strftime("%H:%M")
    return QUIET[0] <= hm < QUIET[1]


# ------------------------------------------------------------------------------------------------------ the sound
def _read(p: Path):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                         capture_output=True, timeout=60).stdout
    return np.frombuffer(raw, np.float32).copy()


def _write(p: Path, x) -> None:
    import numpy as np
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _envelope(x):
    import numpy as np
    return np.convolve(np.abs(x), np.ones(960) / 960, "same")


def bursts(x) -> List[List[float]]:
    """The words as bursts of sound, [start, end, core start, core end] in seconds: the core above 8 % of the peak,
    joined over gaps under 70 ms, the closest joined until there are four; start and end moved out to where the sound
    is under 2 % of the peak (the consonants), never more than 0.12 s."""
    import numpy as np
    env = _envelope(x)
    if not len(env) or env.max() <= 0:
        return []
    hi = (env > env.max() * 0.08).astype(np.int8)
    edges = np.flatnonzero(np.diff(np.concatenate([[0], hi, [0]])))
    out: List[List[float]] = []
    for a, b in zip(edges[::2], edges[1::2]):
        if out and a / SR - out[-1][1] < 0.07:
            out[-1][1] = b / SR
        else:
            out.append([a / SR, b / SR])
    out = [s for s in out if s[1] - s[0] > 0.08]
    while len(out) > 4:                                   # a name of two words: join the closest gap
        g = min(range(len(out) - 1), key=lambda i: out[i + 1][0] - out[i][1])
        out[g:g + 2] = [[out[g][0], out[g + 1][1]]]
    lo = env.max() * 0.02
    for s in out:
        a, b = int(s[0] * SR), min(len(env) - 1, int(s[1] * SR))
        while a > 0 and env[a] > lo and s[0] - a / SR < 0.12:
            a -= 1
        while b < len(env) - 1 and env[b] > lo and b / SR - s[1] < 0.12:
            b += 1
        s[:] = [a / SR, b / SR, s[0], s[1]]
    return out


def splice(x, b) -> Tuple[object, List[float], float]:
    """The four words closer together, each exactly as said: only the silence between two words is shortened, its
    middle taken out, keeping 40 ms after the word before and 60 ms before the next (a soft "S" or "F" lives there;
    cutting into it turned "Stumpf" into "Thug" on 10.28) plus GAPS[i] split between them; 8 ms crossfades. Returns
    the line, where each word starts in it, and where the last word ends (seconds)."""
    import numpy as np
    cuts = []                                        # (from, to) input samples taken out
    for i in range(3):
        e, s = int(b[i][3] * SR), int(b[i + 1][2] * SR)
        ka, kb = int((0.04 + GAPS[i] / 2) * SR), int((0.06 + GAPS[i] / 2) * SR)
        if s - e > ka + kb + int(0.02 * SR):
            cuts.append((e + ka, s - kb))
    a0, e3 = int(b[0][0] * SR), min(len(x), int(b[3][1] * SR))
    xf = int(0.008 * SR)
    out, keep_from = np.zeros(0, np.float32), a0
    for c0, c1 in cuts + [(e3, e3)]:
        seg = x[keep_from:c0].astype(np.float32)
        if len(out) >= xf and len(seg) >= xf:
            ramp = np.linspace(0, 1, xf, dtype=np.float32)
            out[-xf:] = out[-xf:] * (1 - ramp) + seg[:xf] * ramp
            seg = seg[xf:]
        out = np.concatenate([out, seg])
        keep_from = c1

    def moved(t: float) -> float:                    # an input time, in the spliced line
        k = int(t * SR)
        gone = sum((c1 - c0) + xf for c0, c1 in cuts if c1 <= k)
        return (k - a0 - gone) / SR
    starts = [max(0.0, moved(w[0])) for w in b]
    return out, starts, moved(b[3][1])


def _band(x, lo: float, hi: float):
    import numpy as np
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(np.fft.rfft(x) * ((f >= lo) & (f <= hi)), len(x)).astype(np.float32)


def _hall(seconds: float):
    import numpy as np
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = _band(np.random.default_rng(9).standard_normal(n).astype(np.float32), 120, 6000) * np.exp(-t * 3.2 / seconds)
    ir[: int(0.018 * SR)] = 0
    return ir / (np.abs(ir).max() + 1e-9)


def arena(y):
    """The ring's sound on the spliced line: played slower (lower, PITCH), body and bite, a little growl, the slap
    echo, a long hall, the tail faded. The word starts move by 1/PITCH."""
    import numpy as np
    idx = np.arange(0, len(y) - 1, PITCH)
    y = np.interp(idx, np.arange(len(y)), y).astype(np.float32)
    y = y / (np.abs(y).max() + 1e-9)
    y = y + LOW * _band(y, 90, 300) + BITE * _band(y, 2000, 5000)
    y = np.tanh(DRIVE * y / (np.abs(y).max() + 1e-9)) / np.tanh(DRIVE)
    tail = int(max(HALL_S, max(d for d, _g in ECHO) + 0.25) * SR)
    out = np.concatenate([y, np.zeros(tail, np.float32)])
    for d, g in ECHO:
        k = int(d * SR)
        out[k:k + len(y)] += g * _band(y, 200, 4500)
    src = out[: len(y) + int(0.4 * SR)]
    ir = _hall(HALL_S)
    nfft = 1 << (len(src) + len(ir) - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(src, nfft) * np.fft.rfft(ir, nfft), nfft)[: len(out)].astype(np.float32)
    out[: len(wet)] += HALL * wet / (np.abs(wet).max() + 1e-9)
    k = int(0.15 * SR)
    out[-k:] *= np.linspace(1, 0, k)
    return out / (np.abs(out).max() + 1e-9)


# ------------------------------------------------------------------------------------------------------ the takes
def _heard(p: Path, text: str) -> str:
    """What whisper hears in a take ('' when whisper or its model is not here)."""
    if not WHISPER.exists() or not WHISPER_MODEL.exists():
        return ""
    with tempfile.TemporaryDirectory() as td:
        w16 = Path(td) / "t.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p), "-ar", "16000", "-ac", "1", str(w16)],
                       check=True, timeout=60)
        r = subprocess.run([str(WHISPER), "-m", str(WHISPER_MODEL), "-f", str(w16), "-nt", "-np", "-l", "en",
                            "--prompt", text], capture_output=True, text=True, timeout=180)
    return " ".join(r.stdout.split()).lower()


def says_it(heard: str, left: str, right: str) -> bool:
    """The call in order: a word like each word of the left name (at least 60 % alike, so whisper's "stompf" is Stumpf
    and "stone" is not; a first and last name both, so "Josh Orly" is not Josh Hawley), versus, the right name the same
    way, fight. With no whisper on this Mac ('' heard), the sound's four words are the only check."""
    from difflib import SequenceMatcher
    if not heard:
        return True
    ws = re.findall(r"[a-z]+", heard.lower())

    def like(word: str):
        n = re.sub(r"[^a-z]", "", word.lower())
        return lambda w: SequenceMatcher(None, w, n).ratio() >= 0.6

    def name(words: str):
        return [like(w) for w in words.split() if re.search(r"[a-z]", w.lower())]
    steps = (name(left) + [lambda w: re.fullmatch(r"ver(sus|ses|s)|vs", w) is not None] + name(right)
             + [lambda w: w in ("fight", "fite")])
    i = 0
    for ok in steps:
        while i < len(ws) and not ok(ws[i]):
            i += 1
        if i == len(ws):
            return False
        i += 1
    return True


def ranked(takes: List[dict], left: str, right: str, text: str) -> List[Tuple[object, list]]:
    """The takes that say the call and hold four words with real gaps between them, loudest first."""
    import numpy as np
    good = []
    for t in takes:
        x = _read(Path(t["wav"]))
        b = bursts(x)
        if len(b) != 4 or min(b[i + 1][2] - b[i][3] for i in range(3)) < 0.03 or b[-1][1] - b[0][0] > 4.5:
            continue
        if not says_it(_heard(Path(t["wav"]), text), left, right):
            continue
        a, e = int(b[0][0] * SR), int(b[-1][1] * SR)
        good.append((float(np.sqrt(np.mean(x[a:e] ** 2)) / (np.abs(x).max() + 1e-9)), x, b))
    return [(x, b) for _s, x, b in sorted(good, key=lambda g: -g[0])]


# ------------------------------------------------------------------------------------------------------ the call
def _kept(key: str) -> Optional[dict]:
    wav, meta = cache_dir() / f"{key}.wav", cache_dir() / f"{key}.json"
    if not (wav.is_file() and meta.is_file()):
        return None
    try:
        m = json.loads(meta.read_text())
        return {"signal": _read(wav), "starts": m["starts"], "speech": m["speech"], "kept": True}
    except (OSError, ValueError, KeyError):
        return None


def call(left: str, right: str, now: Optional[datetime] = None) -> Tuple[Optional[dict], str]:
    """({"signal" at SR, "starts": {left, versus, right, fight} s, "speech" s}, '') or (None, why the card plays its
    sound alone). `left` and `right` are the names as the announcer says them ("Warren", "Stumpf")."""
    key = _key(left, right)
    got = _kept(key)
    if got:
        return got, ""
    if os.environ.get("CLIP_FACTORY_OFFLINE") == "1":
        return None, "tests run offline: no announcer model"
    for p, what in ((PY, "its environment ~/.venvs/announcer"), (GEN, "vsannounce_gen.py"), (REF, "its reference")):
        if not p.exists():
            return None, f"the announcer is not installed here ({what} is missing)"
    if _quiet(now or datetime.now()):
        return None, f"no new announcer line from {QUIET[0]} to {QUIET[1]} (the night shift's last minutes)"
    text = f"{left}! Versus! {right}! FIGHT!"          # the straight read (D RING's own take in the audition)
    drawn = f"{left}... versus... {right}! FIGHT!"     # the drawn read keeps the words apart when the straight one runs on
    with tempfile.TemporaryDirectory() as td:
        try:
            r = subprocess.run([str(PY), str(GEN), td, str(REF), str(EXAG), str(CFG), str(TEMP), str(TAKES), text, drawn],
                               capture_output=True, text=True, timeout=BUDGET,
                               env={**os.environ, "HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
        except subprocess.TimeoutExpired:
            return None, f"the announcer took over {BUDGET} s"
        takes = []
        for ln in r.stdout.splitlines():
            try:
                takes.append(json.loads(ln))
            except ValueError:
                pass
        if not takes:
            return None, f"the announcer made no take ({(r.stderr or '').strip()[-160:]})"
        signal = None
        for x, b in ranked(takes, left, right, text):          # whisper hears the finished call too, or the next take
            line, starts, speech = splice(x, b)
            sig = arena(line)
            done = Path(td) / "call.wav"
            _write(done, sig)
            if says_it(_heard(done, text), left, right):
                signal = sig
                break
        if signal is None:
            return None, f"none of {len(takes)} takes said \"{text}\" cleanly"
    starts = {w: round(s / PITCH, 3) for w, s in zip(WORDS, starts)}
    speech = round(speech / PITCH, 3)
    cache_dir().mkdir(parents=True, exist_ok=True)
    _write(cache_dir() / f"{key}.wav", signal)
    (cache_dir() / f"{key}.json").write_text(json.dumps({"text": text, "starts": starts, "speech": speech,
                                                         "style": STYLE}))
    return {"signal": signal, "starts": starts, "speech": speech, "kept": False}, ""
