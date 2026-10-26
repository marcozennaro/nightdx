"""Self-calibration: learn how loud stations really are at your place.

After every band scan we compare the measured SNR of each identifiable
station (the only scheduled station on its frequency) with the model's
prediction, and keep a running average of the difference per station and
transmitter site.

How that is used differs by band (tested on ~1,500 recordings from Trieste,
Oct 2026):

* Shortwave: most of the difference is the same for every station - your
  antenna, your local noise and the model's own bias (about -33 dB there).
  That receiver-wide part, the median over all stations, is applied to every
  station, including ones never scanned before; each station's own deviation
  from it is kept within +-10 dB. (Applying each station's raw average instead
  ranked uncalibrated stations above calibrated ones and let single wild values
  through: AUC 0.68 -> 0.72, rank correlation 0.38 -> 0.48 with this change.)
* Medium wave: power, ground wave and co-channel neighbours differ a lot
  from channel to channel, and the per-station averages do help, so they are
  used as learned (the receiver-wide value only for stations not yet learned).

Every learned pair is also appended to scan_pairs.jsonl, so future changes to
these rules can be tested against your real history.
"""

from __future__ import annotations

import json
import threading
import time

from . import config

ALPHA = 0.3            # weight of the newest observation
CAP_DB = 40.0
STATION_CAP_DB = 10.0  # shortwave: a station may differ this much from the receiver-wide value
MIN_RECORDS = 20       # stations needed before a receiver-wide value is trusted
NOT_HEARD_SNR = -18.0  # a frequency we scanned but didn't detect is at most this loud
HISTORY_MAX_BYTES = 20_000_000
_lock = threading.Lock()
_cache: dict | None = None
_receiver: dict[str, float] | None = None


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


def band(entry) -> str:
    return "mw" if entry.khz < 1800 else "sw"


def receiver_offset(b: str) -> float:
    """The part of the learned corrections shared by all stations of a band: the median
    over stations, weighted by how often each was measured. 0 until enough are known."""
    global _receiver
    if _receiver is None:
        out = {}
        for bb in ("sw", "mw"):
            vals = sorted((r["db"], max(1, r["n"])) for r in _load().values()
                          if isinstance(r, dict) and r.get("band") == bb)
            if len(vals) >= MIN_RECORDS:
                half, acc = sum(n for _, n in vals) / 2, 0
                for db, n in vals:
                    acc += n
                    if acc >= half:
                        out[bb] = db
                        break
        _receiver = out
    return _receiver.get(b, 0.0)


def station_offset(entry) -> float | None:
    rec = _load().get(key(entry))
    return rec["db"] if rec else None


def offset(entry) -> float:
    b = band(entry)
    g = receiver_offset(b)
    own = station_offset(entry)
    if own is None:
        return g
    if b == "mw":
        return own
    return g + max(-STATION_CAP_DB, min(STATION_CAP_DB, own - g))


def annotate_bands(entries) -> None:
    """Older learned.json files don't say which band a record belongs to: fill it in from
    the schedule (called when the schedule is loaded)."""
    global _receiver
    data = _load()
    missing = [k for k, r in data.items() if isinstance(r, dict) and "band" not in r]
    if not missing:
        return
    by_key = {}
    for e in entries:
        by_key.setdefault(key(e), band(e))
    with _lock:
        hits = [k for k in missing if k in by_key]
        for k in hits:
            data[k]["band"] = by_key[k]
        if hits:
            _receiver = None
            _path().write_text(json.dumps(data))


def learn(pairs) -> int:
    """pairs: [(entry, prediction, measured_snr_or_-inf)] for uniquely identified stations.
    Only learns 'not heard' when the model thought the path was open and unabsorbed, so a
    closed band or daytime absorption isn't mistaken for a weak transmitter."""
    global _receiver
    data = _load()
    n = 0
    history = []
    now = round(time.time())
    with _lock:
        for e, p, meas in pairs:
            raw = p.snr_db - offset(e)          # prediction without the learned part
            history.append({"t": now, "key": key(e), "khz": e.khz, "raw": round(raw, 1),
                            "meas": None if meas == float("-inf") else round(meas, 1),
                            "p_muf": round(p.p_muf, 2), "absorption": round(p.absorption_db, 1)})
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
            rec["band"] = band(e)
            n += 1
        _receiver = None
        _path().write_text(json.dumps(data))
        _append_history(history)
    return n


def _append_history(items: list[dict]) -> None:
    if not items:
        return
    p = config.data_dir() / "scan_pairs.jsonl"
    try:
        if p.exists() and p.stat().st_size > HISTORY_MAX_BYTES:      # keep the newer half
            lines = p.read_text().splitlines()
            p.write_text("\n".join(lines[len(lines) // 2:]) + "\n")
        with p.open("a") as f:
            f.writelines(json.dumps(x) + "\n" for x in items)
    except OSError:
        pass


def stats() -> tuple[int, int]:
    d = _load()
    return len(d), sum(r["n"] for r in d.values() if isinstance(r, dict))
