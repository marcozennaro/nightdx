"""Settings, stored as a small TOML file in the NightDX home directory."""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

def _default_home() -> Path:
    """Per-user data folder, never inside the code folder:
    macOS ~/Library/Application Support/NightDX, Windows %APPDATA%\\NightDX,
    Linux ~/.local/share/NightDX. Override with the NIGHTDX_HOME environment variable."""
    from platformdirs import user_data_dir
    return Path(user_data_dir("NightDX", appauthor=False, roaming=True))


def _default_recordings() -> str:
    from platformdirs import user_documents_dir
    return str(Path(user_documents_dir()) / "NightDX" / "recordings")


HOME = Path(os.environ.get("NIGHTDX_HOME") or _default_home()).expanduser()
CONFIG_PATH = HOME / "config.toml"


@dataclass
class General:
    setup_done: bool = False          # the first-run setup wizard has been completed


@dataclass
class Location:
    name: str = ""                    # e.g. "Trieste"
    country: str = ""                 # e.g. "Italy" (used in reception reports)
    lat: float = 0.0                  # decimal degrees, + north
    lon: float = 0.0                  # decimal degrees, + east


@dataclass
class Receiver:
    backend: str = "auto"            # auto | library (libairspyhf) | tool (airspyhf_rx)
    airspyhf_rx: str = "airspyhf_rx"
    serial: str = ""                 # empty = first device found
    sample_rate: int = 192000        # 192 kS/s is plenty for one AM channel
    tune_offset_hz: int = 10000      # tune off-center to dodge any DC artefact
    agc: bool = True
    agc_threshold: str = "low"       # low | high
    lna: bool = False
    attenuation: int = 0             # 0..8, 6 dB steps (only when AGC is off)
    # Frequency error of the radio, for the carrier offsets in the log. If a station known to be
    # exactly on frequency reads +0.30 Hz on 1000 kHz, set -0.30.
    freq_correction_ppm: float = 0.0


@dataclass
class Recording:
    duration_s: int = 120
    audio_rate: int = 16000
    audio_bandwidth_hz: int = 4500
    format: str = "flac"             # flac | wav
    output_dir: str = field(default_factory=_default_recordings)


@dataclass
class Stations:
    season: str = "auto"             # auto, or e.g. A26 / B26
    medium_wave: bool = True         # include the MW band (520-1710 kHz)
    min_khz: float = 520
    max_khz: float = 26100
    broadcast_only: bool = True      # drop CW, RTTY, HFDL, time signals...


@dataclass
class Propagation:
    tx_power_kw: float = 100.0       # EiBi has no power data: assumed for all
    rx_antenna_gain_dbi: float = -3.0
    noise_environment: str = "residential"  # quiet | rural | residential | city
    min_score: int = 25


@dataclass
class Classifier:
    backend: str = "auto"            # auto | ast | dsp
    model: str = "MIT/ast-finetuned-audioset-10-10-0.4593"
    window_s: float = 10.0


@dataclass
class Mission:
    start: str = "23:30"             # local time
    end: str = "06:30"
    gap_s: int = 15
    targets: list[str] = field(default_factory=list)   # empty = best overall
    max_per_station: int = 3         # recordings of the same station per night
    repeat_after_min: int = 120      # ...at least this far apart
    min_score: int = 20
    discovery: bool = True           # prefer stations you haven't recorded (see mission.py)
    discovery_min_snr: float = 5.0   # ...but only if heard at least this clearly (dB)
    rest_days: int = 7               # a station counts as "new" again after this many days


@dataclass
class Qsl:
    """Your details for reception reports (see qsl.py and qsl_template.txt)."""
    name: str = ""
    email: str = ""                  # shown in the signature
    gmail_account: str = ""          # which Gmail account opens the draft (optional)
    postal_address: str = ""         # separate lines with " / "
    receiver: str = "AirSpy HF+"
    antenna: str = "20 m long wire antenna"
    years_collecting: int = 20
    server_port: int = 8737          # local report server (127.0.0.1 only)


@dataclass
class Settings:
    general: General = field(default_factory=General)
    location: Location = field(default_factory=Location)
    receiver: Receiver = field(default_factory=Receiver)
    recording: Recording = field(default_factory=Recording)
    stations: Stations = field(default_factory=Stations)
    propagation: Propagation = field(default_factory=Propagation)
    classifier: Classifier = field(default_factory=Classifier)
    mission: Mission = field(default_factory=Mission)
    qsl: Qsl = field(default_factory=Qsl)

    @property
    def lowest_khz(self) -> float:
        """Bottom of the frequency range in use (shortwave only = from 2.3 MHz)."""
        s = self.stations
        return s.min_khz if s.medium_wave else max(s.min_khz, 2300.0)

    @property
    def recordings_dir(self) -> Path:
        return Path(self.recording.output_dir).expanduser()

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        out = ["# NightDX settings. Edit here or via Settings in the app.\n"]
        for f in fields(self):
            out.append(f"\n[{f.name}]\n")
            for k, v in asdict(getattr(self, f.name)).items():
                out.append(f"{k} = {_toml_value(v)}\n")
        path.write_text("".join(out))


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def load(path: Path = CONFIG_PATH) -> Settings:
    s = Settings()
    if path.exists():
        data = tomllib.loads(path.read_text())
        for f in fields(s):
            section = getattr(s, f.name)
            for k, v in data.get(f.name, {}).items():
                if hasattr(section, k):
                    if k == "serial" and isinstance(v, int):   # serial = 0x3952... without quotes
                        v = f"0x{v:016X}"
                    setattr(section, k, type(getattr(section, k))(v))
    else:
        s.save(path)
    return s


def data_dir() -> Path:
    d = HOME / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
