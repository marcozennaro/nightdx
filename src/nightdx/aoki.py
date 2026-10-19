"""AOKI shortwave/medium-wave schedule (Nagoya DX Circle "Bi Newsletter").

A free, frequently updated schedule that - unlike EiBi - gives transmitter
power (kW), antenna azimuth and exact site coordinates for almost every
entry. NightDX uses it twice:
  * to enrich EiBi entries (same frequency, overlapping time) with power,
    beam direction and coordinates - the model's biggest unknowns;
  * to add broadcasts EiBi doesn't list.
The season's file is found from the newsletter's index page, because its
folder changes every season. The site's HTTPS certificate is broken, so the
download uses plain HTTP.
"""

from __future__ import annotations

import io
import json
import re
import urllib.request
import zipfile
from datetime import datetime, timezone

from . import config

INDEX = "http://www1.s2.starcat.ne.jp/ndxc/nnk.htm"
WEEKDAY = {"1": "Su", "2": "Mo", "3": "Tu", "4": "We", "5": "Th", "6": "Fr", "7": "Sa"}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "NightDX/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def download(season: str) -> str | None:
    """Fetch the AOKI list for `season` (e.g. 'A26'); returns the file name or None."""
    page = _fetch(INDEX).decode("shift_jis", errors="replace")
    links = re.findall(r'href="([^"]*?([a-z]{2})' + season.lower() + r'\.zip)"', page, re.I)
    if not links:
        return None
    url = links[0][0].replace("https://", "http://")
    if not url.startswith("http"):
        url = INDEX.rsplit("/", 1)[0] + "/" + url
    z = zipfile.ZipFile(io.BytesIO(_fetch(url)))
    name = next(n for n in z.namelist() if n.lower().endswith(f"{season.lower()}.txt"))
    out = config.data_dir() / f"aoki-{season.lower()}.txt"
    out.write_bytes(z.read(name))
    (config.data_dir() / "aoki.json").write_text(json.dumps({
        "season": season, "file": out.name, "source": url,
        "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
    return out.name


def _latlon(s: str) -> tuple[float, float] | None:
    m = re.match(r"(\d{2})(\d{2})(\d{2})([NS])(\d{3})(\d{2})(\d{2})([EW])", s.strip())
    if not m:
        return None
    lat = int(m[1]) + int(m[2]) / 60 + int(m[3]) / 3600
    lon = int(m[5]) + int(m[6]) / 60 + int(m[7]) / 3600
    return (-lat if m[4] == "S" else lat, -lon if m[8] == "W" else lon)


def _days(field: str) -> str:
    """AOKI '12.4567' (1 = Sunday) -> EiBi-style 'Su,Mo,We,Th,Fr,Sa'; '' = daily."""
    digits = [d for d in field if d in WEEKDAY]
    if not digits or len(set(digits)) == 7:
        return ""
    return ",".join(WEEKDAY[d] for d in sorted(set(digits)))


def parse(text: str) -> list[dict]:
    lines = text.splitlines()
    hdr = next((l for l in lines if l.startswith("FRE ") and "STATION" in l), None)
    if not hdr:
        return []
    c = {n: hdr.index(n) for n in ["STATION", "UTC", "Su-W-Sa", "Language", "Pow", "Azi",
                                   "Location", "ADM", "L/L", "Remarks"]}
    out = []
    for l in lines:
        m = re.match(r"\s*(\d+(?:\.\d+)?)\s", l)
        if not m or len(l) < c["L/L"]:
            continue
        t = re.match(r"(\d{4})-(\d{4})", l[c["UTC"]:c["UTC"] + 9])
        if not t:
            continue
        pow_s = l[c["Pow"] - 3:c["Azi"]].strip()
        azi_s = l[c["Azi"]:c["Location"]].strip()
        ll = _latlon(l[c["L/L"]:c["Remarks"]])
        try:
            power = float(pow_s) if pow_s else None
        except ValueError:
            power = None
        out.append({
            "khz": float(m[1]), "station": l[c["STATION"]:c["UTC"]].strip(),
            "t0": t[1], "t1": t[2], "days": _days(l[c["Su-W-Sa"]:c["Language"]]),
            "language": l[c["Language"]:c["Pow"] - 3].strip(), "power_kw": power,
            "azimuth": None if azi_s in ("", "ND") else float(azi_s) if azi_s.replace(".", "").isdigit() else None,
            "nd": azi_s == "ND", "site": l[c["Location"]:c["ADM"]].strip(),
            "adm": l[c["ADM"]:c["L/L"]].strip(), "lat": ll[0] if ll else None,
            "lon": ll[1] if ll else None, "remarks": l[c["Remarks"]:].strip(),
        })
    return out


def load() -> list[dict]:
    try:
        meta = json.loads((config.data_dir() / "aoki.json").read_text())
        return parse((config.data_dir() / meta["file"]).read_bytes().decode("latin-1"))
    except Exception:
        return []
