"""Medium-wave stations from an FMSCAN / MWLIST "userlist".

MWLIST (mwlist.org) is the reference database for LW/MW broadcasters, with
transmitter coordinates, power and schedules. FMSCAN (fmscan.org, free
registration) exports it for your location as a "Userlist for Perseus / ELAD /
Winradio / SDR Console": choose "LW/MW/SW (0-30 MHz)", CSV, separator Tab.
Drop the downloaded file into the 'mwlist' folder in the NightDX data folder; the
newest file there. (Data is for personal, non-commercial use - see fmscan.org.)

Documented column order (a header row, if present, overrides it):
khz, country, language, program, modulation, location, latitude, longitude,
power towards receiver, max power ERP, main beam, distance, azimuth, dB,
target area, times, days, offset Hz, beacon details, ID
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from . import config

DEFAULT_ORDER = ["khz", "country", "language", "program", "modulation", "location", "lat", "lon",
                 "power_rx", "power_max", "beam", "distance", "azimuth", "db", "target", "times",
                 "days", "offset", "beacon", "id"]
HEADER_HINTS = [  # (field, substrings that identify it in a header cell)
    ("khz", ("khz", "freq")), ("country", ("country", "itu")), ("language", ("lang",)),
    ("program", ("program", "station", "name")), ("modulation", ("mod",)),
    ("location", ("location", "site", "city", "qth")), ("lat", ("lat",)), ("lon", ("lon",)),
    ("power_rx", ("towards", "to rx", "power rx")), ("power_max", ("erp", "max", "power", "kw")),
    ("beam", ("beam",)), ("distance", ("dist",)), ("azimuth", ("azim", "bearing")),
    ("db", ("db",)), ("target", ("target",)), ("times", ("time", "utc", "hours")),
    ("days", ("day",)), ("offset", ("offset",)), ("id", ("id",)),
]
SKIP_MODES = ("drm", "usb", "lsb", "cw", "ssb", "ndb", "beacon", "rtty", "fax", "dsc", "navtex")
_TIME = re.compile(r"(\d{4})\s*-\s*(\d{4})")
_EIBI_COORD = re.compile(r"(\d{1,2})([NS])(\d{2})(?:'(\d{2}))?.?-(\d{1,3})([EW])(\d{2})(?:'(\d{2}))?")


def folder() -> Path:
    d = config.HOME / "mwlist"
    d.mkdir(parents=True, exist_ok=True)
    return d


def newest_file() -> Path | None:
    files = [p for p in folder().iterdir() if p.suffix.lower() in (".csv", ".txt", ".tsv")]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _num(s: str) -> float | None:
    s = (s or "").strip().replace(",", ".")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _coord(s: str, pos: str, neg: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    v = _num(s)
    if v is None:
        return None
    if re.search(r"\d+\D+\d+", s.replace(",", ".").replace(".", "", 1)) and ("°" in s or "'" in s):
        parts = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s.replace(",", "."))]
        v = parts[0] + (parts[1] / 60 if len(parts) > 1 else 0) + (parts[2] / 3600 if len(parts) > 2 else 0)
    if neg in s.upper() and v > 0:
        v = -v
    return v


def _columns(header: list[str] | None) -> dict[str, int]:
    if not header:
        return {name: i for i, name in enumerate(DEFAULT_ORDER)}
    cols: dict[str, int] = {}
    for i, cell in enumerate(header):
        c = cell.strip().lower()
        for field, hints in HEADER_HINTS:
            if field not in cols and any(h in c for h in hints):
                cols[field] = i
                break
    return cols


def parse(text: str) -> list[dict]:
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return []
    sample = "\n".join(lines[:20])
    sep = max(("\t", ";", ","), key=sample.count)
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=sep))
    header = None
    if rows and _num(rows[0][0]) is None:
        header, rows = rows[0], rows[1:]
    cols = _columns(header)
    get = lambda r, f: r[cols[f]].strip() if f in cols and cols[f] < len(r) else ""
    out = []
    for r in rows:
        khz = _num(get(r, "khz"))
        if not khz:
            continue
        if khz > 30000:           # some exports give Hz or MHz
            khz /= 1000
        elif khz < 30:
            khz *= 1000
        mode = get(r, "modulation").lower()
        if any(m in mode for m in SKIP_MODES):
            continue
        prx, pmax = _num(get(r, "power_rx")), _num(get(r, "power_max"))
        power = prx if prx and prx > 0 else pmax
        times = _TIME.findall(get(r, "times")) or [("0000", "2400")]
        out.append({
            "khz": khz, "country": get(r, "country").upper(), "language": get(r, "language"),
            "program": get(r, "program") or f"{khz:g} kHz", "location": get(r, "location"),
            "lat": _coord(get(r, "lat"), "N", "S"), "lon": _coord(get(r, "lon"), "E", "W"),
            "power_kw": power, "target": get(r, "target"), "times": times,
            "days": get(r, "days"),
        })
    return out


def load_entries(settings) -> tuple[list, str]:
    """Entries from the newest userlist in the mwlist folder, plus a status line."""
    from .eibi import Entry, _hhmm
    path = newest_file()
    if not path:
        return [], "MW: EiBi only (no MWLIST file - see F1)"
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    s = settings.stations
    entries = []
    for st in parse(text):
        if not (settings.lowest_khz <= st["khz"] <= s.max_khz) or st["lat"] is None or st["lon"] is None:
            continue
        for t0, t1 in st["times"]:
            e = Entry(st["khz"], _hhmm(t0), _hhmm(t1), st["days"], st["country"], st["program"],
                      st["language"], st["target"], "", "1", "", "",
                      tx_itu=st["country"], tx_site=st["location"], tx_lat=st["lat"],
                      tx_lon=st["lon"])
            e.power_kw = st["power_kw"]
            e.source = "MWLIST"
            entries.append(e)
    n = len({(e.khz, e.station) for e in entries})
    return entries, f"MW: MWLIST, {n} stations"
