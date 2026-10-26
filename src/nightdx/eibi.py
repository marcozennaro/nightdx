"""EiBi shortwave schedule: download, parse, and answer "who is on the air?".

EiBi (http://www.eibispace.de) publishes one CSV per broadcast season:
A = summer (last Sunday of March -> last Sunday of October), B = winter.
The README holds the country, language and transmitter-site code tables,
including coordinates for the sites, which we need for propagation.
"""

from __future__ import annotations

import calendar
import csv
import io
import json
import re
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import cached_property
from pathlib import Path

from . import config
from .regions import REGIONS, region_of

BASE_URL = "http://www.eibispace.de/dx/"
DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
MONTHS = {m: i for i, m in enumerate(calendar.month_abbr) if m}


# --------------------------------------------------------------------------- seasons

def _last_sunday(year: int, month: int) -> date:
    d = date(year, month, calendar.monthrange(year, month)[1])
    return d - timedelta(days=(d.weekday() - 6) % 7)


def season_code(when: datetime | None = None) -> str:
    d = (when or datetime.now(timezone.utc)).date()
    if _last_sunday(d.year, 3) <= d < _last_sunday(d.year, 10):
        return f"A{d.year % 100:02d}"
    if d >= _last_sunday(d.year, 10):
        return f"B{d.year % 100:02d}"
    return f"B{(d.year - 1) % 100:02d}"


def previous_season(code: str) -> str:
    yy = int(code[1:])
    return f"B{(yy - 1) % 100:02d}" if code[0] == "A" else f"A{yy:02d}"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "NightDX/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def download(season: str = "auto") -> str:
    """Fetch the schedule for `season` (or the current one, falling back to the
    previous season if EiBi hasn't published it yet). Returns the season used."""
    ddir = config.data_dir()
    candidates = [season] if season != "auto" else [season_code(), previous_season(season_code())]
    last_err = None
    for code in candidates:
        try:
            csv_bytes = _fetch(f"{BASE_URL}sked-{code.lower()}.csv")
        except Exception as e:  # 404 when the new season isn't out yet
            last_err = e
            continue
        (ddir / f"sked-{code.lower()}.csv").write_bytes(csv_bytes)
        (ddir / "eibi-readme.txt").write_bytes(_fetch(f"{BASE_URL}README.TXT"))
        try:                                   # second source: power, beams, extra stations
            from . import aoki
            aoki.download(code)
        except Exception:
            pass
        try:                                   # third: the broadcasters' technical data
            from . import hfcc
            hfcc.download(code)
        except Exception:
            pass
        (ddir / "eibi.json").write_text(json.dumps({
            "season": code,
            "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entries": csv_bytes.count(b"\n") - 1,
        }))
        return code
    raise RuntimeError(f"Could not download EiBi schedule: {last_err}")


def status() -> dict | None:
    p = config.data_dir() / "eibi.json"
    return json.loads(p.read_text()) if p.exists() else None


# --------------------------------------------------------------------------- README

_COORD = re.compile(r"(\d{1,2})([NS])(\d{2})(?:'(\d{2})\"?)?-(\d{1,3})([EW])(\d{2})(?:'(\d{2})\"?)?")


def _coord(m: re.Match) -> tuple[float, float]:
    lat = int(m[1]) + int(m[3]) / 60 + int(m[4] or 0) / 3600
    lon = int(m[5]) + int(m[7]) / 60 + int(m[8] or 0) / 3600
    return (-lat if m[2] == "S" else lat, -lon if m[6] == "W" else lon)


@dataclass
class Codebook:
    languages: dict[str, str]
    countries: dict[str, str]
    sites: dict[tuple[str, str], tuple[str, float, float]]   # (itu, code) -> (name, lat, lon)

    @classmethod
    def parse(cls, text: str) -> "Codebook":
        text = text.replace("\r", "")
        # The table of contents repeats the headings; take the last occurrence.
        sec = {k: text.rfind(h) for k, h in [
            ("lang", "   I) Language codes."), ("ctry", "   II) Country codes."),
            ("tgt", "   III) Target-area codes."), ("site", "   IV) Transmitter site codes.")]}

        languages = {}
        for line in text[sec["lang"]:sec["ctry"]].splitlines():
            m = re.match(r"^   (-?[A-Z][A-Za-z0-9]{0,3})\s{2,}(.+?)(?:\s+\(|\s+\[|$)", line)
            if m:
                languages[m[1]] = m[2].split(":")[0].strip()

        countries = {}
        for line in text[sec["ctry"]:sec["tgt"]].splitlines():
            m = re.match(r"^   ([A-Z][A-Z0-9]{0,2})\s{2,}(.+)$", line)
            if m:
                countries[m[1]] = m[2].replace("*", "").strip()

        sites: dict[tuple[str, str], tuple[str, float, float]] = {}
        itu = None
        for line in text[sec["site"]:].splitlines():
            m = re.match(r"^   ([A-Z][A-Z0-9]{0,2}):\s*(.*)$", line)
            if m:
                itu, rest = m[1], m[2]
            elif itu and line.startswith("        ") and line.strip():
                rest = line.strip()
            else:
                if line.strip() == "":
                    continue
                itu = None if not line.startswith(" ") else itu
                continue
            sm = re.match(r"^([A-Za-z0-9]{1,3})-(.*)", rest)
            code, name = (sm[1], sm[2]) if sm else ("", rest)
            c = _COORD.search(name)
            if not c:
                continue
            lat, lon = _coord(c)
            name = name[: c.start()].strip(" ,(:") or name
            sites.setdefault((itu, code), (name, lat, lon))
        return cls(languages, countries, sites)


# --------------------------------------------------------------------------- entries

@dataclass
class Entry:
    khz: float
    start_min: int
    end_min: int
    days: str
    itu: str
    station: str
    lang: str
    target: str
    remarks: str
    persistence: str
    start_date: str
    stop_date: str
    # resolved transmitter location (filled by Schedule)
    tx_itu: str = ""
    tx_site: str = ""
    tx_lat: float | None = None
    tx_lon: float | None = None
    power_kw: float | None = None     # known power (MWLIST); None = guess
    source: str = "EiBi"
    azimuth: float | None = None      # antenna beam, degrees (AOKI, HFCC)
    omni: bool = False                # non-directional antenna (AOKI "ND")
    ant_gain: float | None = None     # transmitting antenna gain, dBi (HFCC)
    beam_width: float | None = None   # half-power half-width, degrees (HFCC)
    hfcc: bool = False                # power/beam/site from the HFCC data

    @property
    def time_str(self) -> str:
        f = lambda m: f"{m // 60:02d}{m % 60:02d}"
        return f"{f(self.start_min)}-{f(self.end_min)}"

    @property
    def region(self) -> str:
        return region_of(self.itu)

    @property
    def key(self) -> str:
        return f"{self.khz:g}|{self.station}"

    # ---- is it on the air at a given UTC moment?
    def on_air(self, t: datetime, for_minutes: float = 0) -> bool:
        if not self._date_ok(t) or not self._day_ok(t):
            return False
        m = t.hour * 60 + t.minute + t.second / 60
        s, e = self.start_min, self.end_min
        if s == e or (s == 0 and e == 1440):
            return True
        need_end = m + for_minutes
        if s < e:
            return s <= m and need_end <= e
        # crosses midnight
        return (m >= s and need_end <= e + 1440) or (m < e and need_end <= e)

    def minutes_left(self, t: datetime) -> float:
        m = t.hour * 60 + t.minute + t.second / 60
        e = self.end_min if self.end_min > self.start_min or self.start_min == self.end_min else self.end_min + 1440
        if self.start_min == self.end_min or (self.start_min == 0 and self.end_min == 1440):
            return 1440
        if m < self.start_min and self.end_min < self.start_min:
            m += 1440
        return e - m

    def _date_ok(self, t: datetime) -> bool:
        def ddmm(s, year):
            return date(year, int(s[2:4]), int(s[0:2]))
        try:
            d = t.date()
            if self.start_date and d < ddmm(self.start_date, d.year):
                return False
            if self.stop_date and d > ddmm(self.stop_date, d.year):
                return False
        except ValueError:
            pass
        return True

    def _day_ok(self, t: datetime) -> bool:
        spec = self.days.strip()
        if not spec:
            return True
        wd = t.weekday()
        if re.fullmatch(r"[1-7]+", spec):                      # 1 = Monday
            return str(wd + 1) in spec
        if m := re.fullmatch(r"(\d{1,2})([A-Z][a-z]{2})", spec):  # 10Oct
            return MONTHS.get(m[2]) == t.month and int(m[1]) == t.day
        if m := re.fullmatch(r"(\d)\.([A-Z][a-z])", spec):        # 1.Sa
            return DAYS.index(m[2]) == wd and (t.day - 1) // 7 + 1 == int(m[1]) if m[2] in DAYS else True
        tokens = re.findall(r"([A-Z][a-z])(?:-([A-Z][a-z]))?", spec)
        if not tokens or any(a not in DAYS or (b and b not in DAYS) for a, b in tokens):
            return True   # irr, Test, alt, ... : unknown -> assume maybe
        for a, b in tokens:
            i, j = DAYS.index(a), DAYS.index(b) if b else DAYS.index(a)
            span = range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1))
            if wd in span:
                return True
        return False


def _hhmm(s: str) -> int:
    s = s.strip()
    return int(s[:2]) * 60 + int(s[2:4])


class Schedule:
    def __init__(self, entries: list[Entry], codebook: Codebook, season: str):
        self.entries = entries
        self.codebook = codebook
        self.season = season
        self.mw_status = "MW: EiBi only"
        self.hfcc_status = ""
        self.own_count = 0
        self.own_problems: list[str] = []
        self._resolve_sites()

    @classmethod
    def load(cls, settings: config.Settings | None = None) -> "Schedule":
        settings = settings or config.load()
        st = status()
        if not st:
            raise FileNotFoundError("No EiBi schedule yet - run 'nightdx update' (or File > Update in the TUI).")
        ddir = config.data_dir()
        code = st["season"]
        raw = (ddir / f"sked-{code.lower()}.csv").read_bytes().decode("latin-1")
        codebook = Codebook.parse((ddir / "eibi-readme.txt").read_bytes().decode("latin-1"))
        s = settings.stations
        entries = []
        for row in csv.reader(io.StringIO(raw), delimiter=";"):
            if len(row) < 9 or not row[0][:1].isdigit():
                continue
            try:
                khz = float(row[0])
                t0, t1 = row[1].split("-")
                e = Entry(khz, _hhmm(t0), _hhmm(t1), row[2], row[3], row[4].strip(), row[5],
                          row[6], row[7], row[8], (row[9] if len(row) > 9 else "").strip(),
                          (row[10] if len(row) > 10 else "").split("[")[0].strip())
            except (ValueError, IndexError):
                continue
            if not (settings.lowest_khz <= khz <= s.max_khz):
                continue
            if s.broadcast_only and _is_utility(e):
                continue
            if not _active(e, code):
                continue
            entries.append(e)
        # Medium wave: a full MWLIST/FMSCAN userlist, when present, replaces EiBi's
        # (international-only) MW entries.
        from . import mwlist
        mw, mw_status = (mwlist.load_entries(settings) if settings.stations.medium_wave
                         else ([], "MW: off"))
        if mw:
            entries = [e for e in entries if e.khz >= 1800] + mw
        sched = cls(entries, codebook, code)
        sched.mw_status = mw_status
        sched.merge_aoki(settings, add_mw=not mw)
        from . import hfcc
        hfcc.ensure(code)
        sched.merge_hfcc()
        # your own list (my_stations.csv) comes last and wins over the same kHz + name
        from . import mystations
        own, sched.own_problems = mystations.load(codebook)
        if own:
            mine = {(round(e.khz), e.station.casefold()) for e in own}
            sched.entries = [e for e in sched.entries
                             if (round(e.khz), e.station.casefold()) not in mine] + own
            sched._resolve_sites()                  # own lines without coordinates
        sched.own_count = len(own)
        from . import calibration
        calibration.annotate_bands(sched.entries)
        return sched

    def merge_aoki(self, settings, add_mw: bool = True) -> None:
        """Enrich entries with AOKI power/beam/coordinates; add broadcasts EiBi lacks."""
        from . import aoki
        rows = aoki.load()
        self.aoki_status = ""
        if not rows:
            return
        st = settings.stations
        by_khz: dict[int, list[Entry]] = {}
        for e in self.entries:
            by_khz.setdefault(round(e.khz), []).append(e)
        enriched, added = 0, 0
        for r in rows:
            khz = r["khz"]
            if not (settings.lowest_khz <= khz <= st.max_khz):
                continue
            t0, t1 = _hhmm(r["t0"]), _hhmm(r["t1"])
            hits = [e for k in (round(khz) - 1, round(khz), round(khz) + 1)
                    for e in by_khz.get(k, []) if abs(e.khz - khz) < 0.6
                    and _overlaps(e.start_min, e.end_min, t0, t1)]
            for e in hits:
                # several transmitters can share a frequency: only take this row's power/beam
                # if it is the same site (within 300 km of EiBi's), or EiBi has no exact site
                if r["lat"] is None:
                    continue
                exact = e.tx_lat is not None and "approx" not in (e.tx_site or "")
                if exact and great_circle_km(e.tx_lat, e.tx_lon, r["lat"], r["lon"]) > 300:
                    continue
                if e.power_kw is None and r["power_kw"]:
                    e.power_kw, e.azimuth, e.omni = r["power_kw"], r["azimuth"], r["nd"]
                    enriched += 1
                if not exact:
                    e.tx_lat, e.tx_lon, e.tx_site = r["lat"], r["lon"], r["site"]
            if hits or r["lat"] is None or (khz < 1800 and not add_mw):
                continue
            name = r["station"].title() if r["station"].isupper() else r["station"]
            name = re.sub(r"^R\.\s*", "Radio ", name)       # AOKI's 'R.Romania Int.
            e = Entry(khz, t0, t1, r["days"], r["adm"], name, r["language"], "", "", "1", "", "",
                      tx_itu=r["adm"], tx_site=r["site"], tx_lat=r["lat"], tx_lon=r["lon"])
            e.power_kw, e.azimuth, e.omni, e.source = r["power_kw"], r["azimuth"], r["nd"], "AOKI"
            if st.broadcast_only and _is_utility(e):
                continue
            self.entries.append(e)
            by_khz.setdefault(round(khz), []).append(e)
            added += 1
        self.aoki_status = f"AOKI: +{added} broadcasts, {enriched} with power/beam"

    def merge_hfcc(self) -> None:
        """Add HFCC's site, power and beam to shortwave entries. A row is taken only when it
        is the same transmission: same frequency, overlapping time, and the same site
        (within 100 km of EiBi's) - or, when EiBi only knows the country, a site there."""
        from . import hfcc
        rows = hfcc.load(self.season)
        self.hfcc_status = ""
        if not rows:
            return
        by_khz: dict[int, list[dict]] = {}
        for r in rows:
            by_khz.setdefault(round(r["khz"]), []).append(r)
        matched = 0
        for e in self.entries:
            if e.khz < 2300 or e.source == "own":
                continue
            cands = [r for k in (round(e.khz) - 1, round(e.khz), round(e.khz) + 1)
                     for r in by_khz.get(k, []) if abs(r["khz"] - e.khz) < 0.6
                     and _overlaps(e.start_min, e.end_min, r["t0"], r["t1"])]
            exact = e.tx_lat is not None and "approx" not in (e.tx_site or "")
            if exact:
                cands = [r for r in cands
                         if great_circle_km(e.tx_lat, e.tx_lon, r["lat"], r["lon"]) <= 100]
            else:
                cands = [r for r in cands if r["adm"] in (e.tx_itu, e.itu)]
                if len({r["code"] for r in cands}) > 1:
                    continue                  # several sites in that country: can't tell
            if not cands:
                continue
            r = max(cands, key=lambda r: _overlap_min(e.start_min, e.end_min, r["t0"], r["t1"]))
            e.power_kw, e.azimuth, e.omni = r["power_kw"], r["azimuth"], r["omni"]
            e.ant_gain, e.beam_width, e.hfcc = r["gain_dbi"], r["beam_width"], True
            if not exact:
                e.tx_lat, e.tx_lon, e.tx_site = r["lat"], r["lon"], r["site"]
            matched += 1
        sw = sum(1 for e in self.entries if e.khz >= 2300)
        self.hfcc_status = f"HFCC: {matched} of {sw} SW broadcasts with power/beam"

    def _resolve_sites(self) -> None:
        sites = self.codebook.sites
        by_country: dict[str, list[tuple[float, float]]] = {}
        for (itu, _), (_, lat, lon) in sites.items():
            by_country.setdefault(itu, []).append((lat, lon))
        for e in self.entries:
            if e.tx_lat is not None:          # already located (MWLIST)
                continue
            r = e.remarks.strip()
            m = re.match(r"^/([A-Z][A-Z0-9]{0,2})(?:-([A-Za-z0-9]{1,3}))?", r)
            if m:
                host, code = m[1], m[2] or ""
            else:
                host = e.itu
                code = r if re.fullmatch(r"[A-Za-z0-9]{1,3}", r) else ""
            e.tx_itu = host
            hit = sites.get((host, code)) or sites.get((host, ""))
            if hit:
                e.tx_site, e.tx_lat, e.tx_lon = hit
            elif host in by_country:   # unknown site: centroid of the country's sites
                pts = by_country[host]
                e.tx_lat = sum(p[0] for p in pts) / len(pts)
                e.tx_lon = sum(p[1] for p in pts) / len(pts)
                e.tx_site = f"{self.country_name(host)} (approx.)"

    # ---- lookups
    def country_name(self, itu: str) -> str:
        return self.codebook.countries.get(itu, itu)

    def language_name(self, lang: str) -> str:
        return ", ".join(self.codebook.languages.get(c.strip(), c.strip()) for c in lang.split(",") if c.strip())

    def on_air(self, t: datetime, for_minutes: float = 0) -> list[Entry]:
        return [e for e in self.entries if e.on_air(t, for_minutes)]

    def countries_in_use(self) -> list[tuple[str, str, int]]:
        counts: dict[str, int] = {}
        for e in self.entries:
            counts[e.itu] = counts.get(e.itu, 0) + 1
        return sorted(((c, self.country_name(c), n) for c, n in counts.items()), key=lambda x: x[1])

    # ---- the "India / Africa / Belgium" style selector
    def matcher(self, query: str):
        """Turn a user target ("India", "Africa", "BEL", "lang:Hindi",
        "target:WAf", "BBC") into a predicate over entries."""
        q = query.strip()
        ql = q.lower()
        if ":" in q:
            kind, val = (x.strip().lower() for x in q.split(":", 1))
            if kind in ("lang", "language"):
                return lambda e: val in self.language_name(e.lang).lower() or val == e.lang.lower()
            if kind == "target":
                return lambda e: val == e.target.lower() or val in e.target.lower()
            if kind in ("tx", "site"):
                return lambda e: e.tx_itu.lower() == val
            if kind == "station":
                return lambda e: val in e.station.lower()
        for r in REGIONS:
            if ql == r.lower():
                return lambda e: e.region == r
        codes = {c for c, name in self.codebook.countries.items()
                 if ql == c.lower() or ql == name.lower() or ql == name.lower().split(" (")[0]}
        if codes:
            return lambda e: e.itu in codes
        return lambda e: ql in e.station.lower()


# Broadcast bands in kHz (LW, MW and the SW meter bands, with the usual
# out-of-band fringes where broadcasters actually sit).
BROADCAST_BANDS = [
    (148, 283), (520, 1710), (2300, 2500), (3200, 3400), (3900, 4050), (4750, 5100),
    (5800, 6300), (7200, 7650), (9300, 9950), (11550, 12160), (13550, 13900),
    (15000, 15850), (17450, 17950), (18850, 19050), (21440, 21860), (25600, 26110),
]


def in_broadcast_band(khz: float) -> bool:
    return any(lo <= khz <= hi for lo, hi in BROADCAST_BANDS)


def _active(e: Entry, season: str) -> bool:
    """Drop entries EiBi itself marks as not running (README, entry #9 and #3)."""
    p = e.persistence.strip()
    if p == "8" or (p == "4" and season.startswith("A")) or (p == "5" and season.startswith("B")):
        return False
    return e.days.strip().lower() not in ("alt", "harm", "imod")


def _overlap_min(s1: int, e1: int, s2: int, e2: int) -> int:
    """Minutes two daily UTC intervals (end may wrap past midnight) have in common."""
    def mins(s, e):
        e = e if e > s else e + 1440
        return {m % 1440 for m in range(s, e)}
    return len(mins(s1, e1) & mins(s2, e2))


def great_circle_km(lat1, lon1, lat2, lon2) -> float:
    from .propagation import great_circle
    return great_circle(lat1, lon1, lat2, lon2)[0]


def _overlaps(s1: int, e1: int, s2: int, e2: int) -> bool:
    """Do two UTC time windows (minutes, may wrap midnight) overlap?"""
    def spans(s, e):
        if s == e or (s == 0 and e == 1440):
            return [(0, 1440)]
        return [(s, e)] if s < e else [(s, 1440), (0, e)]
    return any(a < d and c < b for a, b in spans(s1, e1) for c, d in spans(s2, e2))


def _is_utility(e: Entry) -> bool:
    if e.lang.startswith("-") and e.lang != "-MX":
        return True
    if not in_broadcast_band(e.khz):
        return True
    s = e.station.lower()
    return any(w in s for w in ("jammer", "drm", "digital", "volmet", "meteo", "navtex",
                                " usb", " lsb", "channel marker", "time signal",
                                "wwv", "standard time", "test tone"))
