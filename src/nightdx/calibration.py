"""Self-calibration: learn how loud each transmitter really is at your place.

After every band scan we compare the measured SNR of each identifiable
station (the only scheduled station on its frequency) with the model's
prediction, and keep a running average of the difference per station and
transmitter site. That offset is added to future predictions, so wrong power
guesses, your antenna and your local noise get corrected night after night.
"""

from __future__ import annotations

import json
import threading

from . import config

ALPHA = 0.3            # weight of the newest observation
CAP_DB = 40.0
NOT_HEARD_SNR = -18.0  # a frequency we scanned but didn't detect is at most this loud
_lock = threading.Lock()
_cache: dict | None = None


def _path():
    return config.data_dir() / "learned.json"


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_path().read_text())
        except Exception:
            _cache = {}
    return _cache


def key(entry) -> str:
    return f"{entry.station}|{entry.tx_itu}|{entry.tx_site}|{entry.target}"   # same beam


def offset(entry) -> float:
    rec = _load().get(key(entry))
    return rec["db"] if rec else 0.0


def learn(pairs) -> int:
    """pairs: [(entry, prediction, measured_snr_or_-inf)] for uniquely identified stations.
    Only learns 'not heard' when the model thought the path was open and unabsorbed, so a
    closed band or daytime absorption isn't mistaken for a weak transmitter."""
    data = _load()
    n = 0
    with _lock:
        for e, p, meas in pairs:
            raw = p.snr_db - offset(e)          # prediction without the learned part
            if meas == float("-inf"):
                if p.p_muf < 0.7 or p.absorption_db > 6 or raw <= NOT_HEARD_SNR:
                    continue            # band closed or absorbed: says nothing about power
                delta = NOT_HEARD_SNR - raw
            else:
                delta = meas - raw
            delta = max(-CAP_DB, min(CAP_DB, delta))
            rec = data.setdefault(key(e), {"db": delta, "n": 0})
            rec["db"] = round(rec["db"] + ALPHA * (delta - rec["db"]) if rec["n"] else delta, 1)
            rec["n"] += 1
            n += 1
        _path().write_text(json.dumps(data))
    return n


def stats() -> tuple[int, int]:
    d = _load()
    return len(d), sum(r["n"] for r in d.values())
