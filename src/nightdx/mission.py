"""Night missions: unattended recording between two local times.

Every slot the planner looks at what is on the air for at least the next
recording length, keeps the stations matching the user's targets (taken in
turn, so "India, Africa, Belgium" interleave), drops stations already
recorded tonight, and picks the one the propagation model likes best.

A running mission publishes its state to mission.json (data folder) so the TUI
can show progress, and keeps the computer awake. It stops cleanly when the
file mission.stop appears in the data folder (works the same on every OS).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import config, core, propagation, report, scan, solar
from .eibi import Entry, Schedule

STATE_PATH = config.HOME / "mission.json"
SLOT_OVERHEAD_S = 12          # tuning, saving, classification
ANY = "best overall"          # pseudo-target used when no targets are set


@dataclass
class Pick:
    when: datetime
    entry: Entry
    pred: propagation.Prediction
    target: str
    reason: str = ""


def skip_key(e: Entry, any_freq: bool = False) -> str:
    """How a station is written in mission.skip: 'Station@6070' (that frequency) or 'Station'."""
    return e.station if any_freq else f"{e.station}@{e.khz:g}"


def skipped(e: Entry, skip: list[str]) -> bool:
    if not skip:
        return False
    s = {x.strip().lower() for x in skip}
    return e.station.lower() in s or skip_key(e).lower() in s


# Discovery mode: among the stations a scan hears clearly, prefer the ones you are
# missing. Lower tier = picked first; within a tier the loudest wins.
TIER_NAMES = ["never recorded before", "recorded before only as noise",
              "not recorded for a while", "recorded recently / already reported",
              "QSL card already received"]


def station_history() -> dict[str, dict]:
    """{station name (lower case): {'last': datetime, 'heard': bool}} from the logbook."""
    from . import logbook
    out: dict[str, dict] = {}
    for r in logbook.connect().execute(
            "SELECT station, utc_start, label FROM log WHERE error IS NULL AND file IS NOT NULL"):
        h = out.setdefault((r["station"] or "").lower(), {"last": None, "heard": False})
        t = datetime.fromisoformat(r["utc_start"])
        if h["last"] is None or t > h["last"]:
            h["last"] = t
        h["heard"] |= bool(r["label"]) and r["label"] != "noise"
    return out


def window(start_hhmm: str, end_hhmm: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Next (or current) local time window, returned in UTC. 23:30-06:30 spans midnight."""
    now = (now or datetime.now()).astimezone()
    sh, sm = map(int, start_hhmm.split(":"))
    eh, em = map(int, end_hhmm.split(":"))
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)
    if now >= end:                         # tonight's window is over -> tomorrow's
        start, end = start + timedelta(days=1), end + timedelta(days=1)
    elif end - timedelta(days=1) > now:    # we're in the tail of last night's window
        start, end = start - timedelta(days=1), end - timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


class Planner:
    def __init__(self, sched: Schedule, settings: config.Settings, flux: float, kp: float,
                 targets: list[str]):
        self.sched, self.settings, self.flux, self.kp = sched, settings, flux, kp
        self.targets = [t for t in targets if t.strip()] or [ANY]
        self.matchers = {t: (lambda e: True) if t == ANY else sched.matcher(t) for t in self.targets}
        self.done: dict[str, list[datetime]] = {}
        self.turn = 0
        self.history = station_history() if settings.mission.discovery else {}
        self._contacted = self._qsl_keys()

    @staticmethod
    def _qsl_keys() -> tuple[set, set]:
        """Station keys already reported to, and those with a QSL card."""
        try:
            from . import cards, qslstate
            reported = {k for k, v in qslstate.states(check_gmail=False).items()
                        if v["state"] == "contacted"}
            return reported, set(cards.by_key())
        except Exception:
            return set(), set()

    def tier(self, e: Entry, t: datetime) -> int:
        from . import qsl
        h = self.history.get(e.station.lower())
        if h is None:
            tier = 0
        elif not h["heard"]:
            tier = 1
        elif (t - h["last"]).days >= self.settings.mission.rest_days:
            tier = 2
        else:
            tier = 3
        reported, carded = self._contacted
        if reported or carded:
            key = qsl.station_key({"station": e.station, "itu": e.itu})
            if key in carded:
                tier = 4
            elif key in reported:
                tier = max(tier, 3)
        return tier

    def candidates(self, t: datetime) -> list[Entry]:
        minutes = self.settings.recording.duration_s / 60 + 0.5
        skip = self.settings.mission.skip
        return [e for e in self.sched.on_air(t, minutes)
                if any(m(e) for m in self.matchers.values()) and not skipped(e, skip)]

    def choose(self, t: datetime, measured: dict | None = None) -> Pick | None:
        """Next target in turn; with a fresh scan, the loudest station really coming in,
        otherwise (or for unscanned frequencies) the best prediction above min_score."""
        s = self.settings
        rows = scan.order(propagation.rank(self.candidates(t), s, t, self.flux, self.kp), measured)
        if s.mission.discovery:
            pick = self._discover(t, rows)
            if pick:
                return pick
        for i in range(len(self.targets)):
            target = self.targets[(self.turn + i) % len(self.targets)]
            match = self.matchers[target]
            for e, p, m, primary in rows:
                if m is None:
                    ok = p.score >= s.mission.min_score
                else:
                    ok = scan.heard(m) and primary
                if ok and match(e) and self._allowed(e, t):
                    self.turn = (self.turn + i + 1) % len(self.targets)
                    return Pick(t, e, p, target)
        return None

    def _discover(self, t: datetime, rows) -> Pick | None:
        """Discovery: clearly heard (or, without a scan, well predicted) stations, the ones
        you are missing first. Returns None to fall back to plain 'loudest first'."""
        s = self.settings
        good = []
        for e, p, m, primary in rows:
            if m is None:
                if p.score >= max(s.mission.min_score, 60):
                    good.append((self.tier(e, t), -p.score, e, p))
            elif scan.heard(m) and primary and m >= s.mission.discovery_min_snr:
                good.append((self.tier(e, t), -m, e, p))
        good.sort(key=lambda x: (x[0], x[1]))
        for i in range(len(self.targets)):
            target = self.targets[(self.turn + i) % len(self.targets)]
            match = self.matchers[target]
            for tier, _, e, p in good:
                if match(e) and self._allowed(e, t):
                    self.turn = (self.turn + i + 1) % len(self.targets)
                    return Pick(t, e, p, target, f"discovery: {TIER_NAMES[tier]}")
        return None

    def _allowed(self, e: Entry, t: datetime) -> bool:
        times = self.done.get(e.station.lower(), [])
        m = self.settings.mission
        limit = m.max_per_station
        if m.discovery and times and self.tier(e, t) >= 3:
            limit = 1                 # regulars once a night: leave the slots for new stations
        return len(times) < limit and all(
            abs((t - x).total_seconds()) >= m.repeat_after_min * 60 for x in times)

    def mark(self, pick: Pick) -> None:
        self.done.setdefault(pick.entry.station.lower(), []).append(pick.when)


def preview(sched: Schedule, settings: config.Settings, flux: float, kp: float,
            start: datetime, end: datetime, targets: list[str]) -> list[Pick]:
    """Simulate the night without touching the radio."""
    pl = Planner(sched, settings, flux, kp, targets)
    slot = timedelta(seconds=settings.recording.duration_s + settings.mission.gap_s + SLOT_OVERHEAD_S)
    t, out = start, []
    while t + timedelta(seconds=settings.recording.duration_s) <= end:
        pick = pl.choose(t)
        if pick:
            pl.mark(pick)
            out.append(pick)
            t += slot
        else:
            t += timedelta(minutes=5)
    return out


# --------------------------------------------------------------------------- running

def read_state() -> dict:
    try:
        st = json.loads(STATE_PATH.read_text())
    except Exception:
        return {"state": "idle"}
    if st.get("state") not in ("done", "idle", "stopped", "failed") and not _alive(st.get("pid")):
        st["state"] = "stopped"
    return st


def _alive(pid) -> bool:
    from .osutil import pid_alive
    return pid_alive(pid)


STOP_PATH = config.HOME / "mission.stop"


def launch_background(targets: list[str], start: str, end: str, now: bool = False) -> int:
    """Start a mission as a detached process (survives closing the TUI)."""
    from .osutil import spawn_detached
    cmd = [sys.executable, "-m", "nightdx", "night", "--start", start, "--end", end,
           "--targets", ",".join(targets)] + (["--now"] if now else [])
    STOP_PATH.unlink(missing_ok=True)
    return spawn_detached(cmd, config.HOME / "mission.log")


def stop_background() -> bool:
    st = read_state()
    if _alive(st.get("pid")):
        STOP_PATH.write_text("stop")             # the mission notices within a few seconds
        return True
    return False


def prepare_programme(mission_name: str, settings, say=print) -> None:
    """In the morning: analyse the night's recordings (speech recognition) for the reports."""
    from . import logbook, programme
    rows = logbook.rows(logbook.connect(), "mission=? AND file IS NOT NULL AND label != 'noise' "
                        "AND (programme IS NULL OR programme = '')", (mission_name,), 1000)
    if rows:
        say(f"Analysing programme content of {len(rows)} recordings for the QSL reports...")
    for r in rows:
        try:
            programme.analyse(r, settings)
        except Exception as e:
            say(f"  programme analysis failed for {r['station']}: {e}")


def run(settings: config.Settings, targets: list[str], start_hhmm: str, end_hhmm: str,
        start_now: bool = False, log=print) -> str:
    start, end = window(start_hhmm, end_hhmm)
    if start_now:
        start = datetime.now(timezone.utc)
    name = f"night-{start.astimezone():%Y-%m-%d}"
    stop = threading.Event()
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    except (ValueError, AttributeError):
        pass                                     # not in the main thread / not supported
    STOP_PATH.unlink(missing_ok=True)

    def watch_stop_file():
        while not stop.is_set():
            if STOP_PATH.exists():
                STOP_PATH.unlink(missing_ok=True)
                stop.set()
            time.sleep(2)
    threading.Thread(target=watch_stop_file, daemon=True).start()
    state = {"pid": os.getpid(), "mission": name, "targets": targets,
             "start": start.isoformat(), "end": end.isoformat(), "count": 0, "lines": []}

    def publish(**kw):
        state.update(kw)
        STATE_PATH.write_text(json.dumps(state, default=str))

    def say(msg: str):
        line = f"{datetime.now():%H:%M:%S} {msg}"
        log(line)
        state["lines"] = (state["lines"] + [line])[-30:]
        publish()

    # keep the computer awake for as long as we run
    from .osutil import keep_awake
    awake = keep_awake()
    awake.__enter__()
    try:
        sched = Schedule.load(settings)
        sw = solar.current()
        say(f"Mission {name}: {start.astimezone():%H:%M} -> {end.astimezone():%H:%M} local, "
            f"targets {', '.join(targets) or 'best overall'}, {sw.summary}")
        while datetime.now(timezone.utc) < start and not stop.is_set():
            publish(state="waiting", status=f"waiting until {start.astimezone():%H:%M}")
            stop.wait(min(30, (start - datetime.now(timezone.utc)).total_seconds()))
        planner = Planner(sched, settings, sw.flux, sw.kp, targets)
        last_solar = time.time()
        while not stop.is_set():
            now = datetime.now(timezone.utc)
            if now + timedelta(seconds=settings.recording.duration_s) > end:
                break
            if time.time() - last_solar > 3 * 3600:       # space weather drifts overnight
                sw = solar.current()
                planner.flux, planner.kp, last_solar = sw.flux, sw.kp, time.time()
            measured = None
            publish(state="scanning", status="scanning the bands")
            try:
                measured = scan.survey(sched, settings, sw.flux, sw.kp,
                                       entries=planner.candidates(now))
            except Exception as ex:
                say(f"  scan failed ({ex}); choosing from predictions")
            pick = planner.choose(now, measured)
            if not pick:
                publish(state="waiting", status="nothing suitable on the air; retrying in 2 min")
                stop.wait(120)
                continue
            planner.mark(pick)
            e = pick.entry
            if pick.reason:
                say(f"  {pick.reason}")
            publish(state="recording", status=f"{e.khz:g} kHz {e.station}", progress=0)
            row = core.record_entry(e, sched, settings, sw.flux, sw.kp, mission=name, stop=stop,
                                    log=say, progress=lambda f: publish(progress=round(f, 2)))
            publish(count=state["count"] + (0 if row.get("error") else 1))
            stop.wait(settings.mission.gap_s)
        prepare_programme(name, settings, say)
        out = report.write(mission=name, settings=settings)
        say(f"Mission finished: {state['count']} recordings. Report: {out}")
        publish(state="stopped" if stop.is_set() else "done", status=f"report {out}", report=str(out))
        return str(out)
    except Exception as ex:
        say(f"Mission failed: {ex}")
        publish(state="failed", status=str(ex))
        raise
    finally:
        awake.__exit__(None, None, None)
