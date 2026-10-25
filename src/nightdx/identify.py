"""Is the recording really the scheduled station?

NightDX names a recording after the station the schedule lists on that
frequency at that time - a candidate, not an identification. On a busy
channel (medium wave above all) another station is often what you actually
hear. This module collects the evidence for and against the candidate:

* the language Whisper heard in the programme vs the scheduled language,
* the measured signal vs the propagation prediction (a 60 dB carrier from a
  station predicted inaudible is somebody closer),
* the other stations scheduled on the same frequency at that time,
* the carriers measured in the recording (offsets to a fraction of a Hz: a
  fingerprint for comparing with other DXers' logs).

It never declares a station identified: that is the listener's call.
"""

from __future__ import annotations

import json
import re

CHANNEL_DB = 26.0         # carrier per ~12 Hz bin -> SNR in a 5 kHz channel (as in scan.py)
STRONGER_DB = 35.0        # measured this much above the prediction: probably another station
MIN_SNR_DB = 10.0         # ... and clearly audible

# Whisper confuses these with each other: no mismatch inside a group
_GROUPS = [
    {"chinese", "mandarin", "cantonese", "yue", "amoy", "hakka", "min", "chaozhou", "hokkien",
     "wu", "teochew", "minnan"},
    {"serbian", "croatian", "bosnian", "montenegrin", "serbo"},
    {"malay", "indonesian"}, {"hindi", "urdu"}, {"czech", "slovak"},
    {"russian", "ukrainian", "belarusian", "byelorussian"},
    {"spanish", "castellano", "galician", "catalan", "portuguese"},
    {"persian", "farsi", "dari", "tajik"},
    {"norwegian", "nynorsk", "danish", "swedish"},
    {"bulgarian", "macedonian"}, {"bengali", "assamese"}, {"tagalog", "filipino"},
]
_ALIASES = {"eng": "english", "e": "english", "fra": "french", "ger": "german", "rus": "russian",
            "ara": "arabic", "spa": "spanish", "por": "portuguese", "chi": "chinese"}
# Whisper's guesses for languages it rarely gets right on noisy AM audio
_UNRELIABLE = {"latin", "nynorsk", "sanskrit", "welsh", "maori", "hawaiian", "lingala",
               "javanese", "sundanese", "yiddish", "breton", "faroese", "haitian creole"}
_NO_LANGUAGE = {"", "multilingual", "various", "music", "unknown", "mx", "tests", "test",
                "no", "language", "silent", "carrier", "dead"}


def _words(text: str) -> set[str]:
    return {_ALIASES.get(w, w) for w in re.findall(r"[a-z]+", (text or "").lower())}


def language_matches(heard: str, expected: str) -> bool | None:
    """True/False, or None when it can't be judged (no scheduled language, unreliable guess)."""
    exp = _words(expected)
    h = heard.lower().strip()
    if not h or h in _UNRELIABLE or not exp or exp <= _NO_LANGUAGE:
        return None
    family = {h}.union(*(g for g in _GROUPS if h in g))
    return bool(family & exp)


def heard_languages(row) -> list[str]:
    """Languages Whisper recognised in the programme, most frequent first."""
    items = _json_list(row, "programme")
    seen: dict[str, int] = {}
    for it in items:
        lang = (it.get("language") or "").strip()
        if it.get("kind") == "speech" and lang:
            seen[lang] = seen.get(lang, 0) + 1
    return sorted(seen, key=lambda k: -seen[k])


def _json_list(row, key: str) -> list:
    raw = row[key] if key in row.keys() else None
    if isinstance(raw, list):                # a fresh row dict, not yet read back from the log
        return raw
    try:
        return json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []


def _alternatives(row) -> list[dict]:
    return _json_list(row, "alternatives")


def carriers(row) -> list[dict]:
    return _json_list(row, "carriers")


def assess(row) -> dict:
    """{'status': 'doubtful'|'consistent'|'unverified', 'reasons': [...], 'support': [...],
    'notes': [...], 'alternatives': [...], 'carriers': [...]} for one logbook row."""
    reasons, support = [], []
    station, expected = row["station"], row["language"] or ""
    alts = _alternatives(row)

    langs = heard_languages(row)
    judged = [(h, language_matches(h, expected)) for h in langs]
    judged = [(h, ok) for h, ok in judged if ok is not None]
    if judged:
        if any(ok for _, ok in judged):
            support.append(f"speech heard in {', '.join(h for h, ok in judged if ok)}, "
                           f"as scheduled")
        else:
            heard = ", ".join(h for h, _ in judged)
            msg = f"speech heard in {heard}, but {station} is scheduled in {expected}"
            fits = [a for a in alts if any(language_matches(h, a.get("language", "")) for h, _ in judged)]
            if fits:
                msg += " - fits " + ", ".join(f"{a['station']} ({a.get('itu', '')})" for a in fits[:3])
            reasons.append(msg)

    if row["carrier_db"] is not None and row["predicted_snr"] is not None:
        snr = row["carrier_db"] - CHANNEL_DB
        if snr >= MIN_SNR_DB and snr - row["predicted_snr"] >= STRONGER_DB:
            km = f" over {row['distance_km']:,.0f} km" if row["distance_km"] else ""
            reasons.append(f"signal {snr:.0f} dB, far stronger than predicted for {station} "
                           f"({row['predicted_snr']:.0f} dB{km}) - probably a closer station "
                           f"on the same frequency")

    notes = []
    cs = carriers(row)
    if len(cs) > 1:
        notes.append(f"{len(cs)} carriers on the channel: more than one station is on the air")

    status = "doubtful" if reasons else "consistent" if support else "unverified"
    return {"status": status, "reasons": reasons, "support": support, "notes": notes,
            "alternatives": alts, "carriers": cs}


def summary(a: dict) -> str:
    """One line for the terminal."""
    if a["status"] == "doubtful":
        return "probably NOT this station: " + "; ".join(a["reasons"])
    if a["status"] == "consistent":
        return "consistent with the schedule: " + "; ".join(a["support"]) + " (not verified)"
    return "candidate from the schedule - not verified"


def carrier_text(cs: list[dict]) -> str:
    if not cs:
        return ""
    main, rest = cs[0], cs[1:]
    t = f"carrier {main['offset_hz']:+.2f} Hz"
    if rest:
        t += "; also " + ", ".join(f"{c['offset_hz']:+.2f} Hz ({c['rel_db']:.0f} dB)" for c in rest)
    return t


# ----------------------------------------------------------------------------- recording time

def alternatives_for(entry, sched, utc, settings, flux: float, kp: float, limit: int = 6) -> list[dict]:
    """Other stations scheduled on the same frequency now, best predicted first."""
    from . import propagation
    loc, p = settings.location, settings.propagation
    out, seen = [], {entry.station.lower()}
    for e in sched.on_air(utc):
        if abs(e.khz - entry.khz) > 0.6 or e.station.lower() in seen:
            continue
        seen.add(e.station.lower())
        try:
            pr = propagation.predict(e, loc.lat, loc.lon, utc, flux, kp, p.tx_power_kw,
                                     p.rx_antenna_gain_dbi, p.noise_environment)
            score, km = pr.score, round(pr.distance_km)
        except Exception:
            score, km = None, None
        out.append({"station": e.station, "itu": e.itu, "country": sched.country_name(e.itu),
                    "language": sched.language_name(e.lang), "score": score, "km": km})
    out.sort(key=lambda a: -(a["score"] or 0))
    return out[:limit]


def find_carriers(iq, rate: float, span_hz: float = 25.0, max_carriers: int = 3) -> list[dict]:
    """Carriers within +-span_hz of the tuned frequency, to ~0.01 Hz, strongest first.

    `iq` is the complex baseband of the recording (station mixed to 0 Hz) at `rate`.
    Returns [{'offset_hz': ..., 'rel_db': 0 for the strongest, 'snr_db': ...}].
    """
    import numpy as np
    from scipy import signal
    iq = np.asarray(iq, dtype=np.complex64)
    step = max(1, int(rate // (4 * span_hz)))
    sr = rate / step
    if len(iq) < rate * 4:
        return []
    lp = signal.butter(8, 1.3 * span_hz, fs=rate, output="sos")
    x = signal.sosfilt(lp, iq)[::step][int(sr):]           # drop the filter's first second
    n = len(x)
    nfft = 1 << max(16, int(np.ceil(np.log2(n * 8))))
    spec = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(n), nfft))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / sr))
    m = np.abs(f) <= span_hz
    f, p = f[m], 10 * np.log10(spec[m] + 1e-30)
    floor = float(np.median(p))
    bin_hz = f[1] - f[0]
    # +20 dB: well clear of the largest noise peak in ~10^5 bins; 0.8 Hz apart, so the
    # fading/Doppler spread of one sky-wave carrier doesn't count as a second station
    peaks, _ = signal.find_peaks(p, height=floor + 20, prominence=12,
                                 distance=max(1, int(0.8 / bin_hz)))
    if not len(peaks):
        return []
    order = peaks[np.argsort(-p[peaks])][:max_carriers]
    top = p[order[0]]
    out = []
    for k in order:
        if p[k] < top - 40:
            break
        d = 0.0
        if 0 < k < len(p) - 1:                       # parabolic interpolation
            a, b, c = p[k - 1], p[k], p[k + 1]
            den = a - 2 * b + c
            d = 0.5 * (a - c) / den if den else 0.0
        out.append({"offset_hz": round(float(f[k] + d * bin_hz), 2),
                    "rel_db": round(float(p[k] - top), 1), "snr_db": round(float(p[k] - floor), 1)})
    return out
