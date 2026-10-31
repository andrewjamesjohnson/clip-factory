"""THE VS CARD's announcer, the model half (vsannounce.py is the rest): Chatterbox (Resemble AI, MIT) shouts one call
in the voice cloned from our own reference, several takes. Standalone, because Chatterbox lives in its own
environment (~/.venvs/announcer, Python 3.11, torch on the Mac's GPU):

    ~/.venvs/announcer/bin/python vsannounce_gen.py <out dir> <reference wav> <exaggeration> <cfg> <temperature>
        <takes> "Hawley! Versus! Calhoun! FIGHT!" ["Hawley... versus... Calhoun! FIGHT!" ...]
<takes> of each read, in that order; prints one JSON line per take: {"read", "take", "wav", "sr", "seconds"}.
A single word makes the model run on for its whole budget (40 s, 10.28), so it is always given the whole call, and
its token budget is held to about 10 s."""
import json
import sys
from pathlib import Path

import torch
import torchaudio as ta
from chatterbox.tts import ChatterboxTTS

MAX_TOKENS = 250


def main() -> None:
    out, ref = Path(sys.argv[1]), sys.argv[2]
    exag, cfg, temp, takes = float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), int(sys.argv[6])
    reads = sys.argv[7:]
    out.mkdir(parents=True, exist_ok=True)
    model = ChatterboxTTS.from_pretrained(device="mps" if torch.backends.mps.is_available() else "cpu")
    inference = model.t3.inference

    def capped(*a, **k):
        k["max_new_tokens"] = min(int(k.get("max_new_tokens") or MAX_TOKENS), MAX_TOKENS)
        return inference(*a, **k)
    model.t3.inference = capped
    model.prepare_conditionals(ref, exaggeration=exag)
    for r, text in enumerate(reads):
        for k in range(takes):
            torch.manual_seed(1000 + 17 * k)
            wav = model.generate(text, exaggeration=exag, cfg_weight=cfg, temperature=temp)
            p = out / f"take-{r}-{k}.wav"
            ta.save(str(p), wav.cpu(), model.sr)
            print(json.dumps({"read": r, "take": k, "wav": str(p), "sr": model.sr,
                              "seconds": round(wav.shape[-1] / model.sr, 3)}), flush=True)


if __name__ == "__main__":
    main()
