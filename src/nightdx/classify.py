"""Decide whether a recording holds voice, music, or just noise.

Two backends:
  * "ast"  - Audio Spectrogram Transformer fine-tuned on Google AudioSet
             (527 sound classes), run locally with Hugging Face transformers.
             The recording is cut into windows; each window's class
             probabilities are folded into voice / music / noise.
  * "dsp"  - a fallback with no ML dependencies: syllabic (2-8 Hz) envelope
             modulation for speech, spectral stability for music, and
             flatness for noise.
Both also take the receiver's sideband measurement into account: if the
channel had no modulation above the noise floor, it's noise whatever the
model hears in the hiss.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import signal

VOICE_LABELS = {
    "Speech", "Male speech, man speaking", "Female speech, woman speaking",
    "Child speech, kid speaking", "Conversation", "Narration, monologue",
    "Speech synthesizer", "Babbling", "Whispering", "Chant", "Mantra",
}
MUSIC_HINTS = ("music", "singing", "song", "guitar", "piano", "drum", "orchestra",
               "violin", "flute", "trumpet", "choir", "synthesizer", "jingle",
               "instrument", "bass", "beat", "hip hop", "pop", "rock", "jazz", "a capella",
               "sitar", "tabla", "accordion", "organ", "harmonica", "saxophone", "chord")
# Hum/buzz (common on old transmitters) is deliberately absent: it rides on top of
# a programme. A channel with nothing but hum still ends up "noise" because no
# speech or music is detected.
NOISE_LABELS = {
    "Static", "Noise", "White noise", "Pink noise", "Hiss",
    "Environmental noise", "Silence", "Radio", "Sine wave",
    "Electronic tuner", "Beep, bleep", "Rumble", "Distortion", "Tick", "Clicking",
}


@dataclass
class Result:
    label: str                      # voice | music | voice+music | noise
    voice_pct: float
    music_pct: float
    noise_pct: float
    backend: str
    confidence: float
    windows: list[str] = field(default_factory=list)
    top_tags: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (f"{self.label.upper()} (voice {self.voice_pct:.0f}% / music {self.music_pct:.0f}% "
                f"/ noise {self.noise_pct:.0f}%)")


def _fold(windows: list[str], backend: str, conf: float, tags: list[str]) -> Result:
    n = max(len(windows), 1)
    v = 100 * windows.count("voice") / n
    m = 100 * windows.count("music") / n
    z = 100 * windows.count("noise") / n
    if z >= 60:
        label = "noise"
    elif v >= 25 and m >= 25:
        label = "voice+music"
    else:
        label = "voice" if v >= m else "music"
    return Result(label, v, m, z, backend, conf, windows, tags)


# --------------------------------------------------------------------------- AST

_pipe = None


def ast_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except Exception:
        return False


def _ast(model: str):
    global _pipe
    if _pipe is None:
        import os
        import threading
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        # tqdm wants a multiprocessing lock, which can't be created under the TUI
        from tqdm import tqdm
        tqdm.set_lock(threading.RLock())
        from transformers import pipeline
        from transformers.utils import logging as hf_logging
        hf_logging.disable_progress_bar()
        _pipe = pipeline("audio-classification", model=model, device="cpu")
    return _pipe


def ast_windows(audio: np.ndarray, rate: int, model: str, window_s: float) -> list[dict]:
    """Per-window verdicts: [{'start': s, 'label': voice|music|noise, 'conf', 'tags': [...]}]."""
    pipe = _ast(model)
    want = pipe.feature_extractor.sampling_rate
    if rate != want:
        audio = signal.resample_poly(audio, want, rate).astype(np.float32)
        rate = want
    win = int(window_s * rate)
    out = []
    for i in range(0, max(len(audio) - win // 2, 1), win):
        preds = pipe({"raw": audio[i:i + win], "sampling_rate": rate}, top_k=40)
        v = max([p["score"] for p in preds if p["label"] in VOICE_LABELS] or [0])
        m = max([p["score"] for p in preds
                 if any(h in p["label"].lower() for h in MUSIC_HINTS)] or [0])
        z = max([p["score"] for p in preds if p["label"] in NOISE_LABELS] or [0])
        if max(v, m) < 0.12 or z > 1.5 * max(v, m):
            label, conf = "noise", max(z, 1 - max(v, m))
        elif v >= m:
            label, conf = "voice", v
        else:
            label, conf = "music", m
        out.append({"start": i / rate, "label": label, "conf": conf,
                    "tags": [(p["label"], p["score"]) for p in preds[:5]]})
    return out


def classify_ast(audio: np.ndarray, rate: int, model: str, window_s: float) -> Result:
    wins = ast_windows(audio, rate, model, window_s)
    tag_score: dict[str, float] = {}
    for w in wins:
        for label, score in w["tags"][:3]:
            tag_score[label] = tag_score.get(label, 0) + score
    tags = [t for t, _ in sorted(tag_score.items(), key=lambda x: -x[1])[:5]]
    confs = [w["conf"] for w in wins]
    return _fold([w["label"] for w in wins], "ast", float(np.mean(confs)) if confs else 0, tags)


# --------------------------------------------------------------------------- DSP fallback

def classify_dsp(audio: np.ndarray, rate: int, window_s: float = 5.0) -> Result:
    win = int(window_s * rate)
    windows = []
    for i in range(0, max(len(audio) - win // 2, 1), win):
        x = audio[i:i + win]
        if len(x) < rate:
            continue
        f, t, S = signal.stft(x, rate, nperseg=512, noverlap=256)
        P = np.abs(S) ** 2 + 1e-12
        band = (f > 250) & (f < 3500)
        flat = np.exp(np.mean(np.log(P[band]), 0)) / np.mean(P[band], 0)   # 1 = white
        env = P[band].sum(0)
        env = env / (env.mean() + 1e-12)
        fs_env = rate / 256
        fe, pe = signal.welch(env - env.mean(), fs_env, nperseg=min(len(env), 128))
        syll = pe[(fe > 2) & (fe < 8)].sum() / (pe[(fe > 0.5) & (fe < 20)].sum() + 1e-12)
        # music: steady spectral lines -> successive spectra correlate strongly
        Pn = np.log(P[band])
        Pn = Pn - Pn.mean(0)
        corr = np.mean(np.sum(Pn[:, 1:] * Pn[:, :-1], 0) /
                       (np.linalg.norm(Pn[:, 1:], axis=0) * np.linalg.norm(Pn[:, :-1], axis=0) + 1e-12))
        if np.median(flat) > 0.55 and corr < 0.35:
            windows.append("noise")
        elif syll > 0.45 and corr < 0.75:
            windows.append("voice")
        elif corr >= 0.5:
            windows.append("music")
        else:
            windows.append("voice" if syll > 0.3 else "noise")
    return _fold(windows, "dsp", 0.5, [])


# --------------------------------------------------------------------------- entry point

def classify(audio: np.ndarray, rate: int, settings, modulation_db: float | None = None) -> Result:
    c = settings.classifier
    backend = c.backend
    if backend == "auto":
        backend = "ast" if ast_available() else "dsp"
    res = None
    if backend == "ast":
        try:
            res = classify_ast(audio, rate, c.model, c.window_s)
        except Exception as e:  # never lose a night's recording to the classifier
            res = classify_dsp(audio, rate)
            res.backend = f"dsp (ast failed: {str(e).splitlines()[0][:60]})"
    if res is None:
        res = classify_dsp(audio, rate)
    # The receiver saw nothing above the noise floor in the sidebands:
    # whatever the model imagines in the hiss, it's noise.
    if modulation_db is not None and modulation_db < 1.0 and res.label != "noise":
        res = Result("noise", 0, 0, 100, res.backend + "+rf", 0.9, ["noise"] * len(res.windows),
                     res.top_tags)
    return res
