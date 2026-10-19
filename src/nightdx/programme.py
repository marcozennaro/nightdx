"""Programme details for reception reports, from the recording itself.

Stations verify a report by checking what you heard, so the report needs a
few timed programme items. We cut the recording into 10 s windows with the
AudioSet classifier, merge them into speech / music segments, and run
Whisper on the speech segments to recognise the language and translate
what was said into English. Results are cached in the logbook ("programme").

Shortwave audio is noisy and Whisper sometimes "hears" text in noise, so
only windows the classifier calls speech are transcribed, and typical
hallucinations (repeated phrases, "thanks for watching"...) are dropped.
The items are a starting point: check them against the recording.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta

import numpy as np

from . import classify, logbook

WHISPER_MODEL = "openai/whisper-small"
MAX_SPEECH_S = 25           # transcribe at most this much of each speech segment
MAX_ITEMS = 4
HALLUCINATIONS = ("thank you for watching", "thanks for watching", "subscribe", "subtitles",
                  "amara.org", "copyright", "all rights reserved", "www.", "transcribed by",
                  "end of the video", "see you in the next")
MIN_MODULATION_DB = 4.0     # programme barely above the noise: don't try to read words
_lock = threading.Lock()
_whisper = None


def _model():
    global _whisper
    if _whisper is None:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor
        proc = WhisperProcessor.from_pretrained(WHISPER_MODEL)
        model = WhisperForConditionalGeneration.from_pretrained(WHISPER_MODEL)
        model.eval()
        _whisper = (proc, model, torch)
    return _whisper


MIN_LANG_PROB = 0.6         # below this, Whisper is guessing: don't trust the words


def _speech(audio: np.ndarray, rate: int) -> tuple[str, str, float]:
    """(language name, English translation, language confidence) for a 16 kHz speech clip."""
    proc, model, torch = _model()
    from transformers.models.whisper.tokenization_whisper import LANGUAGES
    feats = proc(audio, sampling_rate=rate, return_tensors="pt").input_features
    tok = proc.tokenizer
    with torch.no_grad():
        # language probabilities from the first decoder step
        sot = tok.convert_tokens_to_ids("<|startoftranscript|>")
        logits = model(input_features=feats,
                       decoder_input_ids=torch.tensor([[sot]])).logits[0, -1]
        codes = list(LANGUAGES)
        ids = tok.convert_tokens_to_ids([f"<|{c}|>" for c in codes])
        probs = torch.softmax(logits[ids], dim=-1)
        best = int(torch.argmax(probs))
        code, prob = codes[best], float(probs[best])
        if prob < MIN_LANG_PROB:
            return LANGUAGES[code].title(), "", prob
        out = model.generate(feats, task="translate", language=code, max_new_tokens=120)
    text = proc.batch_decode(out, skip_special_tokens=True)[0].strip()
    return LANGUAGES[code].title(), text, prob


def _clean(text: str) -> str | None:
    t = re.sub(r"\s+", " ", text).strip(" .-")
    words = t.split()
    if len(words) < 3 or any(h in t.lower() for h in HALLUCINATIONS):
        return None
    # repeated phrases are a classic noise hallucination
    if len(set(w.lower() for w in words)) < max(3, len(words) // 3):
        return None
    return " ".join(words[:28]) + (" ..." if len(words) > 28 else "")


def _music_desc(tags) -> str:
    """'music (female singing, guitar)' from the classifier's music-related tags only."""
    score: dict[str, float] = {}
    for t, s in tags:
        if t != "Music" and any(h in t.lower() for h in classify.MUSIC_HINTS):
            score[t] = score.get(t, 0) + s
    names = [t for t, s in sorted(score.items(), key=lambda x: -x[1]) if s > 0.05][:3]
    return "music" + (f" ({', '.join(n.lower() for n in names)})" if names else "")


def analyse(row, settings, force: bool = False) -> list[dict]:
    """[{'utc': 'HH:MM:SS', 'kind': 'speech'|'music', 'language', 'text'}] for a logbook row."""
    if not force and "programme" in row.keys() and row["programme"]:
        return json.loads(row["programme"])
    if not classify.ast_available():
        return []                     # the optional AI package isn't installed
    import soundfile as sf
    audio, rate = sf.read(row["file"], dtype="float32")
    c = settings.classifier
    with _lock:                                 # models are big: one analysis at a time
        wins = classify.ast_windows(audio, rate, c.model, c.window_s)
        segs: list[dict] = []
        for w in wins:
            if segs and segs[-1]["label"] == w["label"]:
                segs[-1]["end"] = w["start"] + c.window_s
                segs[-1]["tags"] += w["tags"]
            else:
                segs.append({"label": w["label"], "start": w["start"],
                             "end": w["start"] + c.window_s, "tags": list(w["tags"])})
        t0 = datetime.fromisoformat(row["utc_start"])
        items = []
        for seg in segs:
            if seg["label"] == "noise" or len(items) >= MAX_ITEMS:
                continue
            utc = (t0 + timedelta(seconds=seg["start"])).strftime("%H:%M:%S")
            if seg["label"] == "music":
                items.append({"utc": utc, "kind": "music", "language": "",
                              "text": _music_desc(seg["tags"])})
                continue
            a = int(seg["start"] * rate)
            b = min(len(audio), a + int(min(seg["end"] - seg["start"], MAX_SPEECH_S) * rate))
            lang, text, prob = "", "", 0.0
            if (row["modulation_db"] or 0) >= MIN_MODULATION_DB:
                try:
                    lang, text, prob = _speech(audio[a:b], rate)
                except Exception:
                    pass
            items.append({"utc": utc, "kind": "speech",
                          "language": lang if prob >= MIN_LANG_PROB else "",
                          "text": _clean(text) or ""})
    logbook.update(logbook.connect(), row["id"], programme=json.dumps(items))
    return items


def as_lines(items: list[dict]) -> list[str]:
    out = []
    for it in items:
        if it["kind"] == "music":
            out.append(f"{it['utc']} UTC - {it['text']}")
        else:
            lang = f" in {it['language']}" if it["language"] else ""
            said = f': "{it["text"]}" (automatic English translation)' if it["text"] else \
                " (words not clear enough to transcribe)"
            out.append(f"{it['utc']} UTC - speech{lang}{said}")
    return out
