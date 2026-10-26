"""A compact HF sky-wave propagation model.

This is a simplified cousin of VOACAP, good enough to rank stations:

  * the great-circle path is split into F2 hops (max ~3500 km each);
  * at each hop's reflection point the solar zenith angle sets a foF2
    estimate, scaled by the 10.7 cm solar flux and depressed by geomagnetic
    activity (Kp); the hop geometry gives the M-factor and so the MUF;
  * D-layer absorption follows the George & Bradley formula, so low
    frequencies on daylit paths are penalised;
  * path loss + assumed transmitter power + a guess at antenna aim
    (from the EiBi target area) give a received level, compared with
    ITU-R P.372 man-made noise to get an SNR;
  * score = P(f below MUF) x P(SNR > 15 dB), with day-to-day variability,
    expressed 0-100 like VOACAP's REL.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from . import calibration
from .regions import TARGET_CENTRES

# Big international broadcasters and relay operators (high power, typ. 100-500 kW).
MAJOR = ("china radio", "cri ", "cnr", "voice of", "radio romania", "bbc", "exterior", "nhk",
         "kbs", "vatican", "vaticana", "all india", "air ", "akashvani", "radio taiwan", "irib",
         "trans world", "twr", "adventist", "awr", "voa", "radio free", "rfa", "liberty",
         "deutsche welle", "radio france", "rfi", "habana", "wrmi", "kuwait", "saudi", "cairo",
         "algerienne", "ifrikya", "marti", "trt", "thailand", "radio australia", "rnz",
         "new zealand", "polish radio", "radio prague", "slovakia", "hcjb", "family radio",
         "pbs", "xinjiang", "tibet", "sound of hope", "denge", "echo of hope", "kim il",
         "furusato", "shiokaze", "dandelion", "rrb", "vietnam", "korea", "mongolia",
         "radio tamazuj", "dabanga", "sudan radio", "ewtn", "wewn", "wwcr", "wbcq", "wtww",
         "radio mi amigo", "brother stair", "overcomer", "brazil", "nacional", "rne")
# Countries whose small private / hobby shortwave stations usually run ~1 kW or less.
SMALL_PRIVATE = {"HOL", "D", "DNK", "FIN", "NOR", "S", "BEL", "I", "IRL", "G", "SVK", "CZE",
                 "AUT", "SUI", "LUX", "UKR", "LTU", "LVA", "EST", "HNG", "SRB", "HRV", "POR",
                 "GRC", "ISL", "CAN", "USA"}


# Which HFCC details go into shortwave predictions (see eibi.merge_hfcc). Tested on 997
# shortwave recordings from Trieste (A26 season): power + beam/antenna raised the ranking of
# heard vs not heard (AUC) from 0.64 to 0.68 and the rank correlation of predicted vs
# measured signal from 0.37 to 0.48 - unlike AOKI's power/beam, which made things worse.
HFCC_USE = {"power", "beam"}


def guess_power_kw(entry, default_kw: float) -> float:
    """EiBi has no power column, so guess from what kind of station it is."""
    # Known power is used for medium wave (MWLIST/AOKI), where it decides the range.
    # On shortwave, AOKI's power and beam data did not improve predictions against
    # band scans from Trieste (Oct 2026: skill 0.77 without vs 0.74-0.75 with), so
    # the target-area guess plus the learned per-station corrections are kept.
    if getattr(entry, "power_kw", None) and (entry.khz < 1800 or entry.source == "MWLIST"):
        return entry.power_kw
    if getattr(entry, "hfcc", False) and entry.power_kw and "power" in HFCC_USE:
        return entry.power_kw
    name = entry.station.lower() + " "
    if any(k in name for k in MAJOR):
        return default_kw
    if entry.itu in SMALL_PRIVATE and entry.tx_itu == entry.itu:
        return 1.0
    if entry.target in (entry.itu, entry.tx_itu) and entry.khz < 6200:
        return 10.0                 # domestic tropical-band stations
    return default_kw

R_EARTH = 6371.0
H_F2 = 320.0
COMPASS = {d: i * 22.5 for i, d in enumerate(
    "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}
NOISE = {  # ITU-R P.372 man-made noise: Fam = c - d*log10(f MHz), dB above kT0b
    "city": (76.8, 27.7), "residential": (72.5, 27.7),
    "rural": (67.2, 27.7), "quiet": (53.6, 28.6),
}


# --------------------------------------------------------------------------- geometry

def great_circle(lat1, lon1, lat2, lon2) -> tuple[float, float]:
    """Distance (km) and initial bearing (deg) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * R_EARTH * math.asin(min(1, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d, (math.degrees(math.atan2(y, x)) + 360) % 360


def intermediate(lat1, lon1, lat2, lon2, frac) -> tuple[float, float]:
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = great_circle(lat1, lon1, lat2, lon2)[0] / R_EARTH
    if d < 1e-9:
        return lat1, lon1
    a = math.sin((1 - frac) * d) / math.sin(d)
    b = math.sin(frac * d) / math.sin(d)
    x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
    y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
    z = a * math.sin(p1) + b * math.sin(p2)
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


def solar_zenith(lat, lon, t: datetime) -> float:
    """Solar zenith angle in degrees (NOAA low-precision formulae)."""
    doy = t.timetuple().tm_yday
    hours = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hours - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    ha = math.radians((hours * 60 + eqt + 4 * lon) / 4 - 180)
    phi = math.radians(lat)
    cz = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.acos(max(-1, min(1, cz))))


# --------------------------------------------------------------------------- ionosphere

# Ionosphere. foF2 follows local solar time: it climbs after sunrise, stays
# high through the afternoon, and decays slowly through the night to a minimum
# just before dawn (DIURNAL, relative to the day level). Defaults were fitted
# to band scans from Trieste; learned per-transmitter corrections live in
# calibration.py.
IONO = {
    "day0": 7.5, "day_flux": 6.0,       # midday foF2 (MHz) at SFI 60, + per 140 SFI units
    "night0": 3.0, "night_flux": 2.5,   # pre-dawn minimum
    "kp_drop": 0.05,                    # foF2 loss per Kp step above 4
    "max_hop_km": 4000.0,
    "absorb_k": 2.0,                    # scale on the George & Bradley absorption
}
DIURNAL = [0.45, 0.38, 0.30, 0.22, 0.12, 0.05, 0.15, 0.40, 0.65, 0.82, 0.92, 0.98,
           1.00, 1.00, 1.00, 0.98, 0.95, 0.90, 0.82, 0.72, 0.63, 0.57, 0.52, 0.48]


def fof2(lat, lon, t: datetime, flux, kp) -> float:
    p = IONO
    s = max(0.0, min(1.5, (flux - 60) / 140))
    day, night = p["day0"] + p["day_flux"] * s, p["night0"] + p["night_flux"] * s
    lt = (t.hour + t.minute / 60 + lon / 15) % 24
    i = int(lt)
    w = DIURNAL[i] + (DIURNAL[(i + 1) % 24] - DIURNAL[i]) * (lt - i)
    f = night + (day - night) * w
    alat = abs(lat)
    if alat > 60:
        f *= 0.8
    elif alat < 25:
        f *= 1 + 0.15 * w            # equatorial anomaly, daytime
    if kp > 4:
        f *= max(0.6, 1 - p["kp_drop"] * (kp - 4) * (1.5 if alat > 50 else 1))
    return f


def hop_geometry(hop_km) -> tuple[float, float, float]:
    """Elevation angle (deg), M-factor (sec of incidence) and slant length (km)."""
    th = hop_km / (2 * R_EARTH)
    rh = R_EARTH + H_F2
    elev = math.atan2(math.cos(th) - R_EARTH / rh, math.sin(th))
    elev = max(elev, math.radians(3))
    sin_i = R_EARTH / rh * math.cos(elev)
    m = 1 / math.sqrt(1 - sin_i ** 2)
    slant = 2 * math.sqrt(rh ** 2 + R_EARTH ** 2 - 2 * R_EARTH * rh * math.cos(th))
    return math.degrees(elev), min(m, 3.8), max(slant, 2 * H_F2)


def absorption_db(f_mhz, zenith, sec_i, flux) -> float:
    """George & Bradley D-layer absorption for one hop."""
    if zenith >= 102:
        return 0.0
    # invert F10.7 = 63.7 + 0.728 R + 0.00089 R^2 for the sunspot number
    r12 = max(0.0, (-0.728 + math.sqrt(0.728 ** 2 + 4 * 0.00089 * (flux - 63.7))) / (2 * 0.00089))
    c = max(0.0, math.cos(math.radians(0.881 * zenith))) ** 1.3
    return 677.2 * sec_i * (1 + 0.0037 * r12) * c / ((f_mhz + 1.2) ** 1.98 + 10.2)


# --------------------------------------------------------------------------- antennas

def tx_gain_dbi(entry, bearing_to_rx: float) -> float:
    if getattr(entry, "hfcc", False) and entry.ant_gain is not None and "beam" in HFCC_USE:
        if entry.omni or entry.beam_width is None or entry.azimuth is None:
            return entry.ant_gain
        diff = abs((bearing_to_rx - entry.azimuth + 180) % 360 - 180)
        # -3 dB at the half-power half-width, sidelobes/back ~20 dB down
        return max(entry.ant_gain - 3 * (diff / entry.beam_width) ** 2, entry.ant_gain - 20)
    tgt = entry.target.strip()
    if not tgt or tgt == "Glo":
        return 8.0
    if tgt == entry.itu or tgt == entry.tx_itu:     # domestic service: NVIS-ish
        return 2.0
    if tgt in COMPASS:
        beam = COMPASS[tgt]
    elif TARGET_CENTRES.get(tgt):
        lat, lon = TARGET_CENTRES[tgt]
        if entry.tx_lat is None:
            return 8.0
        beam = great_circle(entry.tx_lat, entry.tx_lon, lat, lon)[1]
    else:
        return 6.0
    return _beam_gain(abs((bearing_to_rx - beam + 180) % 360 - 180))


def _beam_gain(diff: float) -> float:
    """Typical HF curtain antenna: ~15 dBi on the beam, falling off to the back."""
    if diff <= 20:
        return 15.0
    if diff <= 90:
        return 15.0 - (diff - 20) / 70 * 20
    return -5.0 - (diff - 90) / 90 * 5


# --------------------------------------------------------------------------- prediction

@dataclass
class Prediction:
    score: int            # 0-100
    snr_db: float
    muf_mhz: float
    distance_km: float
    hops: int
    absorption_db: float
    bearing: float        # from receiver towards the transmitter
    tx_gain: float
    note: str = ""
    p_muf: float = 0.0    # chance the frequency is below the path MUF
    p_snr: float = 0.0    # chance the SNR exceeds 15 dB (comfortable AM listening)
    band: str = "SW"      # "MW": ground/sky-wave model below, no MUF
    e_ground: float = 0.0  # MW only: field strengths in dB(uV/m) at your location
    e_sky: float = 0.0
    power_kw: float = 0.0


# --------------------------------------------------------------------------- medium wave
#
# LF/MF propagation is nothing like HF. By day only the ground wave counts
# (a few hundred km, shorter at higher frequencies); after dark the E layer
# returns a sky wave that carries strong stations 1000-2500 km. We use:
#   * ground wave: a fit to ITU-R P.368 curves for average land (3 mS/m);
#   * sky wave: ITU-R P.1147 (annual median at night) with a daylight loss
#     from the solar zenith angle at the path midpoint;
#   * noise: ITU-R P.372 man-made noise, plus atmospheric noise at night,
#     as a noise field strength, so SNR = E - En needs no antenna model.

MW_TOP_KHZ = 1800


def geomag_lat(lat, lon) -> float:
    """Dipole geomagnetic latitude (pole at 80.7N 72.7W)."""
    pl, pn = math.radians(80.7), math.radians(-72.7)
    p, l = math.radians(lat), math.radians(lon)
    return math.degrees(math.asin(math.sin(p) * math.sin(pl) +
                                  math.cos(p) * math.cos(pl) * math.cos(l - pn)))


def mw_fields(khz, dist_km, mid_lat, mid_lon, t: datetime, kp: float) -> tuple[float, float, float]:
    """Ground- and sky-wave field (dB uV/m for 1 kW) and the daylight loss on the sky wave."""
    f = khz / 1000
    d = max(dist_km, 1.0)
    e_ground = 109.5 - 20 * math.log10(d) - 1.4 * d ** 0.55 * f ** 0.7
    p = math.sqrt(d * d + 100.0 ** 2 * 4)                     # slant path via the E layer
    phi = min(abs(geomag_lat(mid_lat, mid_lon)) + 3, 80)
    k = 3.2 + 0.19 * khz ** 0.4 * math.tan(math.radians(phi)) ** 2
    region_a = 110.0 if mid_lon < -25 else 107.0              # ITU Region 2 vs 1/3
    e_sky = region_a - 20 * math.log10(p) - 0.001 * k * p
    z = solar_zenith(mid_lat, mid_lon, t)
    day_loss = 0.0 if z >= 102 else 40.0 if z <= 90 else (102 - z) / 12 * 40
    if kp > 4:
        day_loss += 2 * (kp - 4) * (1 if phi > 48 else 0.5)
    if d < 150:
        e_sky -= 10                                           # steep incidence, near the tx
    return e_ground, e_sky - day_loss, day_loss


def predict_mw(entry, rx_lat, rx_lon, t: datetime, kp: float, default_kw: float,
               noise_env: str) -> Prediction:
    dist, _ = great_circle(entry.tx_lat, entry.tx_lon, rx_lat, rx_lon)
    _, bearing = great_circle(rx_lat, rx_lon, entry.tx_lat, entry.tx_lon)
    mlat, mlon = intermediate(entry.tx_lat, entry.tx_lon, rx_lat, rx_lon, 0.5)
    e_g, e_s, day_loss = mw_fields(entry.khz, dist, mlat, mlon, t, kp)
    power = entry.power_kw if getattr(entry, "power_kw", None) else guess_power_kw(entry, default_kw)
    pw_db = 10 * math.log10(max(power, 0.001))
    e = 10 * math.log10(10 ** (e_g / 10) + 10 ** (e_s / 10)) + pw_db
    f = entry.khz / 1000
    c, dd = NOISE.get(noise_env, NOISE["residential"])
    fa = c - dd * math.log10(f)
    if solar_zenith(rx_lat, rx_lon, t) > 96:                  # night-time atmospheric noise
        fa = max(fa, 82 - 25 * math.log10(f))
    e_noise = fa + 20 * math.log10(f) + 10 * math.log10(5000) - 95.5
    snr = e - e_noise + calibration.offset(entry)
    p_snr = _erf_p((snr - 15) / 8)
    if e_g >= e_s:
        note = "ground wave"
    elif day_loss > 20:
        note = "daylight: sky wave absorbed"
    else:
        note = "night sky wave"
    return Prediction(round(100 * p_snr), snr, 0.0, dist, 0 if e_g >= e_s else 1, day_loss,
                      bearing, 0.0, note, 1.0, p_snr, "MW", e_g + pw_db, e_s + pw_db, power)


def _erf_p(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def predict(entry, rx_lat, rx_lon, t: datetime, flux: float, kp: float,
            tx_power_kw: float = 100, rx_gain_dbi: float = -3,
            noise_env: str = "residential") -> Prediction:
    f = entry.khz / 1000
    if entry.tx_lat is None:
        return Prediction(0, -99, 0, 0, 0, 0, 0, 0, "transmitter location unknown")
    if entry.khz < MW_TOP_KHZ:
        return predict_mw(entry, rx_lat, rx_lon, t, kp, tx_power_kw, noise_env)

    dist, _ = great_circle(entry.tx_lat, entry.tx_lon, rx_lat, rx_lon)
    _, bearing_tx_rx = great_circle(entry.tx_lat, entry.tx_lon, rx_lat, rx_lon)
    _, bearing_rx_tx = great_circle(rx_lat, rx_lon, entry.tx_lat, entry.tx_lon)
    dist = max(dist, 50)
    hops = max(1, math.ceil(dist / IONO["max_hop_km"]))
    hop = dist / hops
    _, m_factor, slant = hop_geometry(hop)
    sec_i_d = min(m_factor * 0.9, 3.5)

    muf, absorb, aurora = 99.0, 0.0, 0.0
    for k in range(hops):
        lat, lon = intermediate(entry.tx_lat, entry.tx_lon, rx_lat, rx_lon, (k + 0.5) / hops)
        z = solar_zenith(lat, lon, t)
        muf = min(muf, fof2(lat, lon, t, flux, kp) * m_factor)
        absorb += IONO["absorb_k"] * absorption_db(f, z, sec_i_d, flux)
        if abs(lat) > 55 and kp > 3:
            aurora += 3 * (kp - 3)

    # power budget
    power_kw = guess_power_kw(entry, tx_power_kw)
    path = slant * hops
    loss = 32.45 + 20 * math.log10(f) + 20 * math.log10(path)
    loss += absorb + aurora + 2 * (hops - 1) + 9           # ground + excess system loss
    fot = 0.85 * muf
    if f > fot:
        loss += min(30, (f - fot) / max(muf - fot, 0.1) * 10)  # above-FOT penalty
    gt = tx_gain_dbi(entry, bearing_tx_rx)
    pr = 10 * math.log10(power_kw * 1000) + gt + rx_gain_dbi - loss

    c, d = NOISE.get(noise_env, NOISE["residential"])
    fam = max(c - d * math.log10(f), 52 - 23 * math.log10(f))   # man-made vs galactic
    noise = fam - 204 + 10 * math.log10(5000)
    snr = pr - noise + calibration.offset(entry)       # learned from your band scans

    p_muf = _erf_p((muf - f) / (0.15 * muf))
    p_snr = _erf_p((snr - 15) / 8)
    note = "skip/over MUF" if f > muf else ("absorbed" if absorb > 25 else "")
    return Prediction(round(100 * p_muf * p_snr), snr, muf, dist, hops, absorb,
                      bearing_rx_tx, gt, note, p_muf, p_snr)


def rank(entries, settings, t: datetime, flux: float, kp: float):
    """Return [(entry, prediction)] best first."""
    loc, p = settings.location, settings.propagation
    out = [(e, predict(e, loc.lat, loc.lon, t, flux, kp, p.tx_power_kw,
                       p.rx_antenna_gain_dbi, p.noise_environment)) for e in entries]
    out.sort(key=lambda x: (-x[1].score, -x[1].snr_db))
    return out
