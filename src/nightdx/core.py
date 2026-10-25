"""Record one scheduled broadcast end to end: tune, capture, save, log, classify."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone
from pathlib import Path

from . import classify, logbook, propagation, receiver, storage
from .config import Settings
from .eibi import Entry, Schedule

warnings.filterwarnings("ignore", category=UserWarning)


def metadata(entry: Entry, sched: Schedule, settings: Settings, pred, utc: datetime,
             mission: str = "") -> dict:
    loc = settings.location
    return {
        "station": entry.station, "khz": entry.khz, "utc_start": utc.isoformat(timespec="seconds"),
        "schedule_utc": entry.time_str, "days": entry.days,
        "itu": entry.itu, "country": sched.country_name(entry.itu), "region": entry.region,
        "lang": entry.lang, "language": sched.language_name(entry.lang), "target": entry.target,
        "tx_site": entry.tx_site or "", "tx_country": sched.country_name(entry.tx_itu),
        "tx_lat": entry.tx_lat, "tx_lon": entry.tx_lon,
        "rx_location": loc.name, "rx_lat": loc.lat, "rx_lon": loc.lon,
        "distance_km": round(pred.distance_km), "bearing": round(pred.bearing),
        "predicted_score": pred.score, "predicted_snr": round(pred.snr_db, 1),
        "muf": round(pred.muf_mhz, 1), "source": f"EiBi {sched.season}",
        "mission": mission, "duration_s": settings.recording.duration_s,
    }


def record_entry(entry: Entry, sched: Schedule, settings: Settings, flux: float, kp: float,
                 mission: str = "", progress=None, stop=None, log=print,
                 do_classify: bool = True, finish=None) -> dict:
    utc = datetime.now(timezone.utc)
    loc, p = settings.location, settings.propagation
    pred = propagation.predict(entry, loc.lat, loc.lon, utc, flux, kp, p.tx_power_kw,
                               p.rx_antenna_gain_dbi, p.noise_environment)
    meta = metadata(entry, sched, settings, pred, utc, mission)
    try:                       # the other stations scheduled here: the name is only a candidate
        from .identify import alternatives_for
        meta["alternatives"] = alternatives_for(entry, sched, utc, settings, flux, kp)
    except Exception:
        meta["alternatives"] = []
    db = logbook.connect()
    log(f"Recording {entry.khz:g} kHz {entry.station} ({meta['country']}) "
        f"for {settings.recording.duration_s}s - predicted {pred.score}%")
    try:
        cap = receiver.record(entry.khz, settings.recording.duration_s, settings, progress, stop,
                              finish)
    except receiver.ReceiverError as e:
        if stop is not None and stop.is_set():
            log("  recording cancelled")
            return {"error": "cancelled"}
        meta["error"] = str(e).splitlines()[0]
        row = {k: meta.get(k) for k in _LOG_KEYS}
        row["error"] = meta["error"]
        row["id"] = logbook.add(db, row)
        log(f"  ! {meta['error']}")
        return row

    meta.update(carrier_db=round(cap.carrier_db, 1), modulation_db=round(cap.modulation_db, 1),
                fading_db=round(cap.fading_db, 1), carriers=cap.carriers or [],
                identification="candidate from the schedule, not verified",
                duration_s=round(len(cap.audio) / cap.rate, 1))
    if cap.carriers:
        meta["carrier_offset_hz"] = cap.carriers[0]["offset_hz"]
    path = storage.audio_path(settings.recordings_dir, utc, entry.khz, entry.station,
                              settings.recording.format)
    storage.save(path, cap.audio, cap.rate, meta)
    row = {k: meta.get(k) for k in _LOG_KEYS}
    row["file"] = str(path)
    row["id"] = logbook.add(db, row)
    log(f"  saved {path.name}  carrier {cap.carrier_db:.0f} dB, modulation {cap.modulation_db:.0f} dB")
    if cap.carriers:
        from .identify import carrier_text
        log(f"  {carrier_text(cap.carriers)}")
    if do_classify:
        row.update(classify_row(row["id"], settings, cap.audio, cap.rate, cap.modulation_db, log))
    return row


def classify_row(rid: int, settings: Settings, audio=None, rate=None, modulation_db=None,
                 log=print) -> dict:
    db = logbook.connect()
    r = db.execute("SELECT * FROM log WHERE id=?", (rid,)).fetchone()
    if audio is None:
        import soundfile as sf
        audio, rate = sf.read(r["file"], dtype="float32")
        modulation_db = r["modulation_db"]
    res = classify.classify(audio, rate, settings, modulation_db)
    fields = dict(label=res.label, voice_pct=round(res.voice_pct), music_pct=round(res.music_pct),
                  noise_pct=round(res.noise_pct), class_backend=res.backend,
                  class_tags=", ".join(res.top_tags))
    logbook.update(db, rid, **fields)
    storage.update_tags(Path(r["file"]), {"classification": res.label, **fields})
    log(f"  -> {res.summary}" + (f"  [{', '.join(res.top_tags[:3])}]" if res.top_tags else ""))
    return fields


_LOG_KEYS = ["utc_start", "khz", "station", "itu", "country", "region", "lang", "language",
             "target", "tx_site", "tx_lat", "tx_lon", "distance_km", "bearing", "predicted_score",
             "predicted_snr", "muf", "carrier_db", "modulation_db", "fading_db", "duration_s",
             "mission", "carriers", "alternatives"]
