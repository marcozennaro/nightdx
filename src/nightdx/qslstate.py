"""Which stations you have already written to - shared by all NightDX versions.

Every Gmail draft NightDX prepares is recorded in <data folder>/qsl_history.json.
Gmail keeps a draft's id after you send it and labels it SENT, so each
station's state is exact:
  "contacted" - at least one report was sent (button turns grey);
  "draft"     - a report is waiting in your Gmail Drafts (orange);
  none        - never written to, or the draft was deleted unsent (green).
Stations are identified by their report address (or name, if none is known),
so all recordings of the same station share one state. The last known status
is saved, so reports show the right colours even before the live check.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config

PATH = config.HOME / "qsl_history.json"
CHECK_EVERY_S = 60
_lock = threading.Lock()
_checked: dict[str, float] = {}


def _load() -> list[dict]:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return []


def _save(entries: list[dict]) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(entries, indent=1))


def _same_home(a: str, b: str) -> bool:
    return a.lower() == b.lower()          # case-insensitive file systems (macOS, Windows)


def record(row, key: str, draft_id: str, url: str, email: str) -> None:
    with _lock:
        entries = _load()
        entries.append({"home": str(config.HOME), "rid": row["id"], "station": row["station"],
                        "khz": row["khz"], "key": key, "email": email, "draft_id": draft_id,
                        "url": url, "status": "draft",
                        "created": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        _save(entries)


def draft_for(rid: int) -> dict | None:
    """The latest draft made for this recording (this NightDX home), if any."""
    for e in reversed(_load()):
        if e["rid"] == rid and _same_home(e["home"], str(config.HOME)):
            return e
    return None


def refresh() -> None:
    """Ask Gmail about drafts not yet known to be sent (at most once a minute each)."""
    from . import gmail
    with _lock:
        entries = _load()
        changed = False
        for e in entries:
            if e.get("status") in ("sent", "gone"):
                continue                       # final states
            if time.time() - _checked.get(e["draft_id"], 0) < CHECK_EVERY_S:
                continue
            st = gmail.draft_status(e["draft_id"])
            _checked[e["draft_id"]] = time.time()
            if st != e.get("status"):
                e["status"] = st
                changed = True
        if changed:
            _save(entries)


def states(check_gmail: bool = True) -> dict[str, dict]:
    """{station key: {'state': 'contacted'|'draft', 'date': 'DD Mon', 'station'}}."""
    if check_gmail:
        try:
            refresh()
        except Exception:
            pass                               # offline / not connected: use saved status
    out: dict[str, dict] = {}
    for e in _load():
        st = e.get("status", "draft")
        if st == "gone":
            continue
        state = "contacted" if st == "sent" else "draft"
        date = datetime.fromisoformat(e["created"]).strftime("%d %b")
        cur = out.get(e["key"])
        if cur is None or (state == "contacted" and cur["state"] != "contacted"):
            out[e["key"]] = {"state": state, "date": date, "station": e["station"]}
    return out
