"""The listening log: one SQLite row per recording."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import config

COLUMNS = """
    id INTEGER PRIMARY KEY,
    utc_start TEXT, khz REAL, station TEXT, itu TEXT, country TEXT, region TEXT,
    lang TEXT, language TEXT, target TEXT, tx_site TEXT, tx_lat REAL, tx_lon REAL,
    distance_km REAL, bearing REAL, predicted_score INTEGER, predicted_snr REAL, muf REAL,
    carrier_db REAL, modulation_db REAL, duration_s REAL, file TEXT, mission TEXT,
    label TEXT, voice_pct REAL, music_pct REAL, noise_pct REAL, class_backend TEXT,
    class_tags TEXT, notes TEXT, error TEXT, fading_db REAL, programme TEXT
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.HOME / "logbook.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute(f"CREATE TABLE IF NOT EXISTS log ({COLUMNS})")
    have = {r[1] for r in db.execute("PRAGMA table_info(log)")}
    for col, kind in (("fading_db", "REAL"), ("programme", "TEXT")):
        if col not in have:                           # logbooks from older versions
            db.execute(f"ALTER TABLE log ADD COLUMN {col} {kind}")
    return db


def add(db: sqlite3.Connection, row: dict) -> int:
    keys = [k for k in row if k != "id"]
    cur = db.execute(f"INSERT INTO log ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})",
                     [json.dumps(row[k]) if isinstance(row[k], (list, dict)) else row[k] for k in keys])
    db.commit()
    return cur.lastrowid


def update(db: sqlite3.Connection, rid: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = [json.dumps(v) if isinstance(v, (list, dict)) else v for v in fields.values()]
    db.execute(f"UPDATE log SET {sets} WHERE id=?", [*vals, rid])
    db.commit()


def rows(db: sqlite3.Connection, where: str = "1=1", args=(), limit: int = 1000) -> list[sqlite3.Row]:
    return db.execute(f"SELECT * FROM log WHERE {where} ORDER BY utc_start DESC LIMIT ?",
                      [*args, limit]).fetchall()


def missions(db: sqlite3.Connection) -> list[str]:
    return [r[0] for r in db.execute(
        "SELECT mission FROM log WHERE mission IS NOT NULL AND mission != '' "
        "GROUP BY mission ORDER BY MAX(utc_start) DESC")]
