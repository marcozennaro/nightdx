"""Band scan: measure which scheduled frequencies are actually coming in.

The HF+ delivers 768 kS/s, i.e. ~600 kHz of usable spectrum per capture -
a whole shortwave band in half a second. We group the frequencies we care
about into such tiles, capture each, average a high-resolution spectrum
(~12 Hz bins) and compare the carrier on each scheduled frequency with the
local noise floor around it.

The carrier level in a 12 Hz bin is converted to an approximate SNR in a
5 kHz AM channel so it reads like the model's predicted SNR.
"""

from __future__ import annotations

import json
import math
import subprocess
import time

import numpy as np

from . import config
from .receiver import find_rx

SR = 768_000
NFFT = 65536                      # 11.7 Hz bins
USABLE_HZ = 560_000               # stay inside the HF+ anti-alias filter
CAPTURE_S = 0.5
SETTLE_S = 0.08
SEARCH_HZ = 80                    # carriers sit within a few Hz of nominal
NOISE_SPAN_HZ = 20_000            # local noise floor: median within +-20 kHz
BIN_TO_CHANNEL_DB = 10 * math.log10(5000 / (SR / NFFT))   # ~26 dB
DETECT_DB = 8.0                   # carrier must be this far above the floor (per bin)
MAX_AGE_S = 30 * 60


def tiles(khz_list) -> list[tuple[float, list[float]]]:
    """Group frequencies into captures of at most USABLE_HZ; returns (centre_hz, [khz...])."""
    out, group = [], []
    for k in sorted(set(khz_list)):
        if group and (k - group[0]) * 1000 > USABLE_HZ:
            out.append(group)
            group = []
        group.append(k)
    if group:
        out.append(group)
    # centre between two 5 kHz channels so the tuning point never sits on a carrier
    return [(((g[0] + g[-1]) / 2) * 1000 + 2500, g) for g in out]


def _capture(center_hz: float, settings) -> np.ndarray:
    n = int((CAPTURE_S + SETTLE_S) * SR)
    rx = settings.receiver
    cmd = [find_rx(settings), "-f", f"{center_hz / 1e6:.6f}", "-a", str(SR), "-n", str(n),
           "-r", "stdout", "-g", "on" if rx.agc else "off", "-m", "on" if rx.lna else "off"]
    if rx.serial:
        cmd += ["-s", rx.serial]
    p = subprocess.run(cmd, capture_output=True, timeout=20)
    x = np.frombuffer(p.stdout, dtype=np.complex64)
    if len(x) < n * 0.8:
        raise RuntimeError("no samples from the HF+ (is another program using it?)")
    return x[int(SETTLE_S * SR):]


def _measure(x: np.ndarray, center_hz: float, khz_list) -> dict[float, float]:
    frames = x[: len(x) // NFFT * NFFT].reshape(-1, NFFT)
    psd = np.fft.fftshift((np.abs(np.fft.fft(frames * np.hanning(NFFT), axis=1)) ** 2).mean(0))
    freqs = np.fft.fftshift(np.fft.fftfreq(NFFT, 1 / SR)) + center_hz
    df = SR / NFFT
    out = {}
    for k in khz_list:
        i = int(round((k * 1000 - freqs[0]) / df))
        w = int(SEARCH_HZ / df) + 1
        span = int(NOISE_SPAN_HZ / df)
        if i - span < 0 or i + span >= NFFT:
            continue
        floor = np.median(psd[i - span:i + span])
        out[k] = float(10 * np.log10(psd[i - w:i + w + 1].max() / floor))
    return out


def scan(khz_list, settings, progress=None) -> dict:
    """Measure every frequency in khz_list. Returns {'time', 'carrier': {khz: dB per bin}}."""
    from .hfdev import HFPlus
    groups = tiles(khz_list)
    carrier: dict[float, float] = {}
    try:
        with HFPlus(SR, settings.receiver) as hf:          # fast: one open, many retunes
            for n, (center, ks) in enumerate(groups):
                if progress:
                    progress(n, len(groups), center / 1e6)
                carrier.update(_measure(hf.capture(center, CAPTURE_S), center, ks))
    except RuntimeError as e:
        if "open" in str(e) or not groups:
            raise
        for n, (center, ks) in enumerate(groups):          # fallback: airspyhf_rx per tile
            if f"{ks[0]:g}" in carrier:
                continue
            if progress:
                progress(n, len(groups), center / 1e6)
            carrier.update(_measure(_capture(center, settings), center, ks))
    now = time.time()
    prev = load() or {"carrier": {}, "t": {}}
    result = {"time": now, "carrier": dict(prev["carrier"]), "t": dict(prev.get("t", {}))}
    for k, v in carrier.items():                     # merge: keep other bands' recent results
        result["carrier"][f"{k:g}"] = round(v, 1)
        result["t"][f"{k:g}"] = now
    result["scanned"] = len(carrier)
    result["heard"] = sum(1 for v in carrier.values() if v >= DETECT_DB)
    (config.data_dir() / "scan.json").write_text(json.dumps(result))
    return result


def survey(sched, settings, flux: float, kp: float, entries=None, progress=None) -> dict:
    """Scan everything on the air (or `entries`) and let the model learn from it."""
    from datetime import datetime, timezone
    from . import calibration, propagation
    t = datetime.now(timezone.utc)
    ents = entries if entries is not None else sched.on_air(t, settings.recording.duration_s / 60)
    started = time.time()
    res = scan(sorted({e.khz for e in ents}), settings, progress)
    by: dict[str, list] = {}
    for e in ents:
        by.setdefault(f"{e.khz:g}", []).append(e)
    loc, pp = settings.location, settings.propagation
    pairs = []
    for k, es in by.items():
        if len({x.station for x in es}) != 1 or k not in res["carrier"]:
            continue                                  # co-channel: can't tell who we heard
        e = es[0]
        pr = propagation.predict(e, loc.lat, loc.lon, t, flux, kp, pp.tx_power_kw,
                                 pp.rx_antenna_gain_dbi, pp.noise_environment)
        pairs.append((e, pr, snr(res, e.khz)))
    res["learned"] = calibration.learn(pairs)
    res["seconds"] = round(time.time() - started, 1)
    return res


def load() -> dict | None:
    p = config.data_dir() / "scan.json"
    try:
        r = json.loads(p.read_text())
    except Exception:
        return None
    return r if time.time() - r["time"] < MAX_AGE_S else None


def snr(result: dict | None, khz: float) -> float | None:
    """Measured SNR in a 5 kHz channel; -inf if scanned but not heard; None if not scanned."""
    if not result:
        return None
    k = f"{khz:g}"
    c = result["carrier"].get(k)
    if c is None or time.time() - result.get("t", {}).get(k, result["time"]) > MAX_AGE_S:
        return None
    return c - BIN_TO_CHANNEL_DB if c >= DETECT_DB else float("-inf")


def heard(m: float | None) -> bool:
    return m is not None and m > float("-inf")


def order(pairs, result: dict | None):
    """[(entry, prediction)] -> [(entry, prediction, measured_snr, primary)] best first.

    `primary` is False for the other stations sharing a frequency with a better-predicted
    one: a carrier there can't be attributed to them. Heard stations come first (loudest
    first), then not-yet-scanned ones by prediction, then those scanned and not heard."""
    best: dict[str, tuple] = {}
    for e, p in pairs:
        k = f"{e.khz:g}"
        if k not in best or p.score > best[k][1].score:
            best[k] = (e, p)
    rows = [(e, p, snr(result, e.khz), best[f"{e.khz:g}"][0] is e) for e, p in pairs]

    def key(r):
        e, p, m, primary = r
        if heard(m) and primary:
            return (0, -m, -p.score)
        if m is None:
            return (1, 0, -p.score)
        if heard(m):
            return (2, 0, -p.score)                  # co-channel with a better candidate
        return (3, 0, -p.score)
    return sorted(rows, key=key)
