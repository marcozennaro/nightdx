"""Space weather from NOAA SWPC: 10.7 cm solar flux and planetary Kp index."""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass

from . import config

FLUX_URL = "https://services.swpc.noaa.gov/json/f107_cm_flux.json"
KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
MAX_AGE_S = 3 * 3600


@dataclass
class SpaceWeather:
    flux: float = 120.0
    kp: float = 2.0
    updated: str = "defaults (offline)"

    @property
    def summary(self) -> str:
        storm = " STORM" if self.kp >= 5 else (" unsettled" if self.kp >= 4 else "")
        return f"SFI {self.flux:.0f}  Kp {self.kp:.1f}{storm}"


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "NightDX/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def update() -> SpaceWeather:
    flux_rows = _get_json(FLUX_URL)
    flux = float(max(flux_rows, key=lambda r: r["time_tag"])["flux"])
    kp_rows = _get_json(KP_URL)
    if isinstance(kp_rows[0], dict):
        last = kp_rows[-1]
        kp, tag = float(last["Kp"]), last["time_tag"]
    else:  # older list-of-lists format with a header row
        last = kp_rows[-1]
        kp, tag = float(last[1]), last[0]
    sw = SpaceWeather(flux, kp, tag[:16].replace("T", " ") + " UTC")
    (config.data_dir() / "solar.json").write_text(json.dumps(sw.__dict__ | {"fetched": time.time()}))
    return sw


def current(allow_network: bool = True) -> SpaceWeather:
    p = config.data_dir() / "solar.json"
    cached = json.loads(p.read_text()) if p.exists() else None
    if cached and time.time() - cached.get("fetched", 0) < MAX_AGE_S:
        return SpaceWeather(cached["flux"], cached["kp"], cached["updated"])
    if allow_network:
        try:
            return update()
        except Exception:
            pass
    if cached:
        return SpaceWeather(cached["flux"], cached["kp"], cached["updated"] + " (stale)")
    return SpaceWeather()
