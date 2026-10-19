"""Received QSL cards, shared by all NightDX versions.

Cards are stored in <NightDX data folder>/qsl_cards/ with an index in
qsl_cards.json next to it. Each card belongs to a recording (the one you
reported) and, through the station key (see qsl.station_key), to the
station: every other recording of that station shows "QSL received".
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config

ROOT = config.HOME
DIR = ROOT / "qsl_cards"
INDEX = ROOT / "qsl_cards.json"
IMAGE = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
CONVERT = {".heic", ".heif", ".tif", ".tiff", ".bmp"}       # -> JPEG with macOS sips
ALLOWED = IMAGE | CONVERT | {".pdf"}
MAX_BYTES = 25 * 1024 * 1024
_lock = threading.Lock()


def _load() -> list[dict]:
    try:
        return json.loads(INDEX.read_text())
    except Exception:
        return []


def _save(cards: list[dict]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(cards, indent=1, ensure_ascii=False))


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:40] or "station"


def add(row, key: str, filename: str, data: bytes, received: str = "", notes: str = "") -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED:
        raise ValueError(f"unsupported file type {ext or '?'} (use JPG, PNG, PDF or HEIC)")
    if len(data) > MAX_BYTES:
        raise ValueError("file larger than 25 MB")
    DIR.mkdir(parents=True, exist_ok=True)
    cid = uuid.uuid4().hex[:10]
    base = f"{row['utc_start'][:10]}_{_slug(row['station'])}_{cid}"
    path = DIR / f"{base}{ext}"
    path.write_bytes(data)
    if ext in CONVERT:                         # browsers can't show HEIC/TIFF
        from . import osutil
        jpg = osutil.to_jpeg(path)
        if jpg:
            path.unlink()
            path = jpg
    card = {"id": cid, "home": str(config.HOME), "rid": row["id"], "station": row["station"],
            "khz": row["khz"], "utc_start": row["utc_start"], "country": row["country"] or "",
            "key": key, "file": path.name, "received": received or datetime.now().strftime("%Y-%m-%d"),
            "notes": notes, "added": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with _lock:
        cards = _load()
        cards.append(card)
        _save(cards)
    return card


def remove(card_id: str) -> dict | None:
    with _lock:
        cards = _load()
        gone = next((c for c in cards if c["id"] == card_id), None)
        if gone:
            cards = [c for c in cards if c["id"] != card_id]
            _save(cards)
            try:
                (DIR / gone["file"]).unlink()
            except FileNotFoundError:
                pass
    return gone


def all_cards() -> list[dict]:
    return sorted(_load(), key=lambda c: c["received"], reverse=True)


def by_key() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in _load():
        out.setdefault(c["key"], []).append(c)
    return out


def for_recording(rid: int) -> list[dict]:
    home = str(config.HOME).lower()
    return [c for c in _load() if c["rid"] == rid and c["home"].lower() == home]


def is_image(card: dict) -> bool:
    return Path(card["file"]).suffix.lower() in IMAGE
