"""Hardware-free tests: run on macOS, Windows and Linux (see .github/workflows/ci.yml)."""

import math
import os
import tempfile
from datetime import datetime, timezone

os.environ.setdefault("NIGHTDX_HOME", tempfile.mkdtemp(prefix="nightdx-test-"))

from nightdx import aoki, config, eibi, mwlist, propagation, qsl, scan  # noqa: E402
from nightdx.eibi import Entry  # noqa: E402


def entry(khz=9420, lat=37.98, lon=23.73, itu="GRC", station="Voice of Greece", target="Eu"):
    return Entry(khz, 0, 1440, "", itu, station, "E", target, "", "1", "", "",
                 tx_itu=itu, tx_site="Avlis", tx_lat=lat, tx_lon=lon)


def test_season_codes():
    assert eibi.season_code(datetime(2026, 7, 1, tzinfo=timezone.utc)) == "A26"
    assert eibi.season_code(datetime(2026, 12, 1, tzinfo=timezone.utc)) == "B26"
    assert eibi.season_code(datetime(2027, 2, 1, tzinfo=timezone.utc)) == "B26"
    assert eibi.previous_season("A26") == "B25"


def test_on_air_across_midnight():
    e = Entry(6000, 23 * 60, 2 * 60, "", "CUB", "Radio Habana Cuba", "E", "NAm", "", "1", "", "")
    assert e.on_air(datetime(2026, 10, 7, 0, 30, tzinfo=timezone.utc))
    assert not e.on_air(datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc))
    assert eibi._overlaps(23 * 60, 120, 60, 180) and not eibi._overlaps(600, 700, 800, 900)


def test_great_circle_and_prediction():
    d, brg = propagation.great_circle(45.65, 13.78, 41.9, 12.5)     # Trieste -> Rome
    assert 400 < d < 450 and 180 < brg < 220
    p = propagation.predict(entry(), 45.65, 13.78,
                            datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc), 120, 2)
    assert 0 <= p.score <= 100 and p.distance_km > 1000 and not math.isnan(p.snr_db)


def test_medium_wave_day_night():
    e = entry(khz=999, lat=45.65, lon=13.78 + 10 / 0.7)
    e.power_kw = 100
    day = propagation.predict(e, 45.65, 13.78, datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc), 100, 2)
    night = propagation.predict(e, 45.65, 13.78, datetime(2026, 10, 7, 23, 0, tzinfo=timezone.utc), 100, 2)
    assert day.band == night.band == "MW" and night.snr_db > day.snr_db + 10


def test_scan_tiles_cover_all():
    ks = [531, 999, 1602, 5900, 6200, 9400, 9900]
    tiles = scan.tiles(ks)
    assert sorted(k for _, g in tiles for k in g) == ks
    assert all((g[-1] - g[0]) * 1000 <= scan.USABLE_HZ for _, g in tiles)


def test_aoki_parse():
    text = ("A26 Shortwave Frequecy List\n"
            "FRE   STATION                         UTC       Su-W-Sa Language             Pow Azi Location                ADM L/L             Remarks\n"
            " 9730 VOICE OF VIETNAM                1900-1930 1234567 English              100 320 Hanoi-Sontay            VTN 210812N1052506E VOV a25\n"
            "  531 Radio Algeria                   0000-2400  23456  Arabic               600  ND F'Kirina                ALG 354409N0072146E ENRS\n")
    rows = aoki.parse(text)
    assert len(rows) == 2
    assert rows[0]["power_kw"] == 100 and rows[0]["azimuth"] == 320 and abs(rows[0]["lat"] - 21.136) < 0.01
    assert rows[1]["nd"] and rows[1]["days"] == "Mo,Tu,We,Th,Fr"


def test_mwlist_parse_variants():
    tab = ("khz\tcountry\tlanguage\tprogram\tmodulation\tlocation\tlatitude\tlongitude\t"
           "power towards receiver\tmax power ERP\tbeam\tdistance\tazimuth\tdB\ttarget\ttimes\tdays\n"
           "540\tHNG\tHungarian\tKossuth Radio\tAM\tSolt\t46,8031\t19,0028\t1000\t2000\t\t433\t75\t60\t\t0400-2200\t\n"
           "1440\tLUX\tChinese\tCRI\tDRM\tMarnach\t50.05\t6.08\t300\t300\t\t800\t300\t40\t\t\t\n")
    rows = mwlist.parse(tab)
    assert len(rows) == 1 and rows[0]["power_kw"] == 1000 and abs(rows[0]["lat"] - 46.8031) < 1e-6
    rows = mwlist.parse("594;D;German;Test;AM;Somewhere;50,1;8,7;0,5;1;;700;300;20;;0500-1800;1234567\n")
    assert rows[0]["power_kw"] == 0.5 and rows[0]["times"] == [("0500", "1800")]


def test_contact_matching():
    assert (qsl.contact("Radio Romania Int.", "ROU") or {}).get("email")
    assert (qsl.contact("Radio Exterior España", "E") or {}).get("email")   # accents ignored
    assert qsl.contact("Radio Europa", "UKR") is None or \
        qsl.contact("Radio Europa", "UKR").get("itu") in (None, "UKR")      # no cross-country mixups


def test_sinpo_ranges():
    row = {"carrier_db": 70, "modulation_db": 25, "fading_db": 1.5, "label": "voice"}  # 44 dB SNR
    s = qsl.sinpo(row)
    assert all(1 <= x <= 5 for x in s) and s[0] == 5


def test_settings_roundtrip(tmp_path):
    s = config.Settings()
    s.location.name, s.location.lat = "Testville", 12.5
    s.stations.medium_wave = False
    path = tmp_path / "config.toml"
    s.save(path)
    t = config.load(path)
    assert t.location.name == "Testville" and t.location.lat == 12.5
    assert t.lowest_khz >= 2300


def test_every_module_imports():
    """Catches platform-specific code at import time (e.g. Windows-only APIs)."""
    import importlib
    import pkgutil
    import nightdx
    for m in pkgutil.iter_modules(nightdx.__path__):
        if m.name != "__main__":
            importlib.import_module(f"nightdx.{m.name}")


def test_pid_alive_is_safe():
    from nightdx import osutil
    assert osutil.pid_alive(os.getpid())          # must not kill ourselves on Windows
    assert not osutil.pid_alive(999999)
    assert osutil.pid_alive(os.getpid())


def test_recording_through_library_stream(monkeypatch):
    """The library path (used on Windows) with simulated driver blocks of 1024 samples:
    an AM carrier modulated by 1 kHz must come out as a 1 kHz tone."""
    import numpy as np
    from nightdx import hfdev, receiver

    class FakeHF:
        def __init__(self, sr, rx):
            self.sr = sr

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def stream(self, freq_hz, total, settle_s=0.0):
            t = np.arange(total) / self.sr
            offset = -10_000                       # station is 10 kHz below the tuned frequency
            iq = 1e-3 * (1 + 0.7 * np.sin(2 * np.pi * 1000 * t)) * np.exp(2j * np.pi * offset * t)
            iq += 1e-6 * (np.random.randn(total) + 1j * np.random.randn(total))
            iq = iq.astype(np.complex64)
            for i in range(0, total, 1024):
                yield iq[i:i + 1024]

    monkeypatch.setattr(hfdev, "HFPlus", FakeHF)
    s = config.Settings()
    s.receiver.backend = "library"
    cap = receiver.record(6000, 3, s)
    assert abs(len(cap.audio) / cap.rate - 3) < 0.1
    spec = np.abs(np.fft.rfft(cap.audio))
    peak_hz = np.argmax(spec[10:]) + 10
    assert abs(peak_hz * cap.rate / len(cap.audio) - 1000) < 20
    assert cap.carrier_db > 40 and cap.modulation_db > 20
