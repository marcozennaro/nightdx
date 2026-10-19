"""Write recordings to disk with the station metadata embedded.

FLAC gets Vorbis comments (standard TITLE/ARTIST/... plus custom fields such
as FREQUENCY_KHZ), WAV gets a RIFF LIST/INFO chunk. A JSON sidecar with the
full record is always written next to the audio.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import soundfile as sf


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:40] or "unknown"


def audio_path(out_dir: Path, utc, khz: float, station: str, fmt: str) -> Path:
    day = out_dir / utc.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    return day / f"{utc:%Y%m%d_%H%M%SZ}_{khz:g}kHz_{slug(station)}.{fmt}"


def save(path: Path, audio, rate: int, meta: dict) -> None:
    fmt = path.suffix.lstrip(".").upper()
    title = f"{meta['station']} {meta['khz']:g} kHz {meta['utc_start'][:16]}Z"
    comment = (f"{meta['khz']:g} kHz | {meta['country']} | {meta['language']} | "
               f"target {meta['target']} | tx {meta['tx_site']} | "
               f"rx {meta['rx_location']} | predicted {meta['predicted_score']}%")
    with sf.SoundFile(path, "w", rate, 1, subtype="PCM_16", format=fmt) as f:
        f.title = title
        f.artist = meta["station"]
        f.album = f"NightDX {meta['utc_start'][:10]}"
        f.date = meta["utc_start"]
        f.comment = comment
        f.software = "NightDX"
        f.genre = "Shortwave"
        f.write(audio)
    if fmt == "FLAC":
        from mutagen.flac import FLAC
        tags = FLAC(path)
        for k, v in meta.items():
            if v is not None and not isinstance(v, (dict, list)):
                tags[k.upper()] = str(v)
        tags.save()
    write_sidecar(path, meta)


def write_sidecar(path: Path, meta: dict) -> None:
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str))


def update_tags(path: Path, extra: dict) -> None:
    """Add classification results to an existing recording's tags and sidecar."""
    side = path.with_suffix(".json")
    meta = json.loads(side.read_text()) if side.exists() else {}
    meta.update(extra)
    write_sidecar(path, meta)
    if path.suffix.lower() == ".flac" and path.exists():
        from mutagen.flac import FLAC
        tags = FLAC(path)
        for k, v in extra.items():
            if not isinstance(v, (dict, list)):
                tags[k.upper()] = str(v)
        tags.save()
