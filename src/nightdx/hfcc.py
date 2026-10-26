"""HFCC: the broadcasters' own coordinated shortwave schedule (hfcc.org public data).

Every season the members of the High Frequency Coordination Conference file
their requirements in the ITU's fixed-column format: frequency, times,
transmitter site, power, azimuth of the beam and antenna type - exactly what
EiBi lacks. HFCC lists what was *registered*, and not all of it goes on the
air, so NightDX uses it only to add those technical details to EiBi entries
(matched by frequency, time and site); it never adds broadcasts of its own.
"""

from __future__ import annotations

import io
import json
import math
import re
import zipfile

from . import config

URL = "https://hfcc.org/data/{s}/{s}allx2.zip"


def _path():
    return config.data_dir() / "hfcc.json"


def _coord(s: str) -> float | None:
    """'52N08' / '004E38' -> decimal degrees."""
    m = re.fullmatch(r"(\d{2,3})([NSEW])(\d{2})", s.strip())
    if not m:
        return None
    v = int(m[1]) + int(m[3]) / 60
    return -v if m[2] in "SW" else v


def parse_sites(text: str) -> dict[str, tuple[str, str, float, float]]:
    """site.txt -> {code: (name, admin, lat, lon)}"""
    out = {}
    for line in text.splitlines():
        if line.startswith(";") or len(line) < 50:
            continue
        lat, lon = _coord(line[39:44]), _coord(line[45:51])
        if lat is not None and lon is not None:
            out[line[0:3].strip()] = (line[4:34].strip(), line[35:38].strip(), lat, lon)
    return out


def parse_antennas(text: str) -> dict[str, str]:
    """antenna.txt -> {code: definition}, e.g. '216': 'AHR(S)4/4/0.5'"""
    out = {}
    for line in text.splitlines():
        if line.startswith(";") or len(line) < 5:
            continue
        out[line[0:3].strip()] = line[4:54].strip()
    return out


def antenna(definition: str) -> tuple[float | None, float | None]:
    """(gain dBi, half-power half-width deg) for an ITU-R BS.705 antenna definition;
    a width of None means omnidirectional. (None, None) if unknown."""
    d = definition.upper()
    if m := re.match(r"[ACE]?HR(?:\(S\))?(\d+)/(\d+)/", d):         # curtain arrays
        width, height = int(m[1]), int(m[2])
        return min(24.0, 9.0 + 10 * math.log10(width * height)), max(8.0, 50.0 / width)
    if d.startswith("LPH"):
        return 12.0, 35.0
    if d.startswith("LPV"):
        return 8.0, 60.0
    if d.startswith("RH"):
        return 16.0, 15.0
    if re.match(r"C[HT]\d", d):          # dipoles for (near-)vertical incidence: little gain far away
        return 3.0, None
    if d.startswith(("HQ", "HX")):
        return 5.0, None
    if d.startswith("VM"):
        return 2.0, None
    return None, None


def parse(text: str, sites: dict, antennas: dict) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if line.startswith(";") or len(line) < 95:
            continue
        try:
            khz = float(line[0:5])
            t0, t1 = line[6:10], line[11:15]
            site = line[47:50].strip()
            power = float(line[51:55] or 0)
            az = line[56:63].strip()
            ant = line[68:71].strip()
        except ValueError:
            continue
        if line[94:95] not in ("D", " "):        # AM only: N = DRM, T = SSB
            continue
        s = sites.get(site)
        if not s:
            continue
        gain, width = antenna(antennas.get(ant, ""))
        rows.append({"khz": khz, "t0": int(t0[:2]) * 60 + int(t0[2:]),
                     "t1": int(t1[:2]) * 60 + int(t1[2:]), "site": s[0].title(), "code": site,
                     "adm": line[113:116].strip(), "lat": s[2], "lon": s[3],
                     "power_kw": power or None,
                     "azimuth": int(az) if az.isdigit() else None,
                     "gain_dbi": gain, "beam_width": width,
                     "omni": width is None and gain is not None})
    return rows


def download(season: str) -> str:
    """Fetch and store this season's HFCC data. Returns a status line."""
    from .eibi import _fetch
    data = _fetch(URL.format(s=season.lower()))
    z = zipfile.ZipFile(io.BytesIO(data))
    names = {n.lower().split("/")[-1]: n for n in z.namelist()}
    read = lambda n: z.read(names[n]).decode("latin-1")
    sched = next(n for n in names if n.endswith("all00.txt"))
    rows = parse(read(sched), parse_sites(read("site.txt")), parse_antennas(read("antenna.txt")))
    _path().write_text(json.dumps({"season": season.upper(), "rows": rows}))
    return f"HFCC {season.upper()}: {len(rows)} transmissions"


def load(season: str | None = None) -> list[dict]:
    try:
        d = json.loads(_path().read_text())
    except Exception:
        return []
    if season and d.get("season") != season.upper():
        return []
    return d["rows"]


def ensure(season: str) -> None:
    """Fetch the season's data if we don't have it (at most one try a day: it's optional)."""
    import time
    if load(season):
        return
    mark = config.data_dir() / "hfcc.tried"
    try:
        if time.time() - mark.stat().st_mtime < 86400:
            return
    except OSError:
        pass
    mark.write_text(season)
    try:
        download(season)
    except Exception:
        pass
