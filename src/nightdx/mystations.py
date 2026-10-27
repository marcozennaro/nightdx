"""Your own stations: a small text file next to the EiBi/AOKI/MWLIST schedules.

For broadcasters the big lists miss (local and pirate stations, a new
frequency, a one-off special broadcast) or to correct an entry. Each line is
one broadcast; your lines replace a list entry with the same frequency and
station name. Edit it from Settings > My stations list.
"""

from __future__ import annotations

import csv
import re

from . import config

TEMPLATE = """\
# NightDX - my stations. One broadcast per line, fields separated by ";".
# Lines starting with # are ignored. Save the file and NightDX reloads it by itself.
# Your lines replace a schedule entry with the same kHz and station name.
#
# kHz ; UTC ; Days ; Station ; Country ; Language ; Target ; Latitude ; Longitude ; kW
#
#  UTC       0000-2400 = all day, or e.g. 1900-2130
#  Days      empty = every day, 1234567 (1 = Monday), or Mo-Fr / Sa,Su as in EiBi
#  Station   station name (required)
#  Country   ITU code as in EiBi (I, D, SVN, USA, ...) or the country's name
#  Language  code (E, I, ...) or name (English, Italian, ...)
#  Target    optional, e.g. Eu, NAm
#  Latitude/Longitude of the transmitter, decimal degrees (south/west negative) -
#            optional: without them NightDX uses the country's transmitter sites
#  kW        optional transmitter power
#
# Examples (remove the # in front to use them; times and coordinates are illustrative):
# 1170;0000-2400;;Radio Capodistria;SVN;Italian
# 6070;0900-1500;Sa,Su;Radio Channel 292;D;English;Eu;48.61;11.56;10
"""


def path():
    return config.HOME / "my_stations.csv"


def ensure_file():
    p = path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TEMPLATE, encoding="utf-8")
    return p


def _num(s: str) -> float | None:
    s = (s or "").strip().replace(",", ".")
    return float(s) if s else None


def _hhmm(s: str) -> int:
    s = s.strip().replace(":", "")
    if not re.fullmatch(r"\d{4}", s):
        raise ValueError(f"time '{s}' should be like 1900")
    h, m = int(s[:2]), int(s[2:])
    if h > 24 or m > 59:
        raise ValueError(f"time '{s}' is not a time")
    return h * 60 + m


def parse(text: str, codebook=None) -> tuple[list, list[str]]:
    """(entries, problems) - a bad line is skipped and reported, never fatal."""
    from .eibi import Entry
    countries = {v.lower(): k for k, v in (codebook.countries if codebook else {}).items()}
    languages = {v.lower(): k for k, v in (codebook.languages if codebook else {}).items()}
    entries, problems = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = [c.strip() for c in next(csv.reader([line], delimiter=";"))] + [""] * 10
        try:
            try:
                khz = float(row[0].replace(",", "."))
            except ValueError:
                raise ValueError(f"'{row[0][:20]}' is not a frequency in kHz")
            t0, t1 = (row[1] or "0000-2400").split("-")
            if not row[3]:
                raise ValueError("the station name is missing")
            itu = row[4]
            itu = countries.get(itu.lower(), itu.upper() if len(itu) <= 3 else itu)
            lang = languages.get(row[5].lower(), row[5])
            e = Entry(khz, _hhmm(t0), _hhmm(t1), row[2], itu, row[3], lang, row[6], "",
                      "", "", "", source="own")
            lat, lon, kw = _num(row[7]), _num(row[8]), _num(row[9])
            if (lat is None) != (lon is None):
                raise ValueError("give both latitude and longitude, or neither")
            if lat is not None:
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("latitude/longitude out of range")
                e.tx_lat, e.tx_lon, e.tx_itu, e.tx_site = lat, lon, itu, "own coordinates"
            e.power_kw = kw
        except (ValueError, IndexError) as err:
            problems.append(f"line {n}: {err}")
            continue
        entries.append(e)
    return entries, problems


def load(codebook=None) -> tuple[list, list[str]]:
    p = path()
    if not p.exists():
        return [], []
    return parse(p.read_text(encoding="utf-8", errors="replace"), codebook)
