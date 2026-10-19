"""nightdx command line. With no command it opens the menu-driven TUI."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import config


def _targets(s: str | None, settings) -> list[str]:
    if s is None:
        return settings.mission.targets
    return [t.strip() for t in s.split(",") if t.strip()]


def _utc_at(hhmm: str | None) -> datetime:
    now = datetime.now(timezone.utc)
    if not hhmm:
        return now
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="nightdx", description="Unattended shortwave listening with an AirSpy HF+")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("tui", help="menu-driven interface (default)")
    sub.add_parser("setup", help="run the setup wizard (location, bands, radio, optional features)")
    sub.add_parser("update", help="download the EiBi schedule and space weather")
    sub.add_parser("serve", help="serve the reports and the 'Send report' buttons")
    sub.add_parser("gmail-setup", help="connect your Gmail account (creates drafts only)")
    b = sub.add_parser("best", help="stations most likely to be heard now")
    b.add_argument("--targets", help='e.g. "India,Africa,Belgium" (default: all)')
    b.add_argument("--at", help="UTC time HH:MM (default now)")
    b.add_argument("-n", type=int, default=25)
    r = sub.add_parser("record", help="record a frequency now")
    r.add_argument("khz", type=float)
    r.add_argument("--seconds", type=int)
    p = sub.add_parser("plan", help="preview what a night mission would record")
    n = sub.add_parser("night", help="run a night mission (unattended)")
    for x in (p, n):
        x.add_argument("--targets")
        x.add_argument("--start", help="local HH:MM")
        x.add_argument("--end", help="local HH:MM")
    n.add_argument("--now", action="store_true", help="start immediately, run until --end")
    c = sub.add_parser("classify", help="(re)classify recordings")
    c.add_argument("--all", action="store_true", help="redo every recording, not just pending ones")
    rp = sub.add_parser("report", help="write the listening report")
    rp.add_argument("--mission")
    rp.add_argument("--since", help="UTC ISO date, e.g. 2026-10-04")
    a = ap.parse_args(argv)
    settings = config.load()

    if a.cmd in (None, "tui", "setup"):
        # The first start after installing can take a while on Windows (antivirus scans,
        # Python compiling the libraries) - say so instead of showing a blank window.
        print("Starting NightDX... (the very first start can take a minute)", flush=True)
        from .tui import run
        run(setup=a.cmd == "setup")
        return

    if a.cmd == "serve":
        from . import server
        server.serve_forever(settings)
        return

    if a.cmd == "gmail-setup":
        from . import gmail
        print("Opening the browser for Google's consent page...")
        print("Connected:", gmail.setup(settings.qsl.gmail_account))
        return

    if a.cmd == "update":
        from . import eibi, solar
        print("EiBi season:", eibi.download(settings.stations.season), eibi.status())
        print("Space weather:", solar.update().summary)
        return

    from . import solar
    from .eibi import Schedule, Entry

    if a.cmd == "best":
        from . import propagation
        sched, sw, t = Schedule.load(settings), solar.current(), _utc_at(a.at)
        targets = _targets(a.targets, settings) if a.targets else []
        ents = sched.on_air(t, settings.recording.duration_s / 60)
        if targets:
            ms = [sched.matcher(x) for x in targets]
            ents = [e for e in ents if any(m(e) for m in ms)]
        print(f"{t:%Y-%m-%d %H:%M} UTC  {settings.location.name}  {sw.summary}")
        seen = set()
        for e, pr in propagation.rank(ents, settings, t, sw.flux, sw.kp):
            if e.key in seen:
                continue
            seen.add(e.key)
            print(f"{e.khz:>7g}  {pr.score:>3}%  {pr.snr_db:>5.0f}dB  {e.station[:28]:<28} "
                  f"{sched.country_name(e.itu)[:18]:<18} {sched.language_name(e.lang)[:14]:<14} "
                  f"{e.target:<5} {pr.distance_km:>6.0f}km {e.time_str}")
            if len(seen) >= a.n:
                break
        return

    if a.cmd == "record":
        from . import core
        if a.seconds:
            settings.recording.duration_s = a.seconds
        sched, sw = Schedule.load(settings), solar.current()
        now = datetime.now(timezone.utc)
        hits = [e for e in sched.on_air(now) if abs(e.khz - a.khz) < 0.6]
        entry = hits[0] if hits else Entry(a.khz, 0, 0, "", "XUU", f"Unknown {a.khz:g} kHz",
                                           "", "", "", "", "", "")
        core.record_entry(entry, sched, settings, sw.flux, sw.kp)
        return

    if a.cmd in ("plan", "night"):
        from . import mission
        start = a.start or settings.mission.start
        end = a.end or settings.mission.end
        targets = _targets(a.targets, settings)
        if a.cmd == "night":
            mission.run(settings, targets, start, end, start_now=a.now)
            return
        sched, sw = Schedule.load(settings), solar.current()
        t0, t1 = mission.window(start, end)
        picks = mission.preview(sched, settings, sw.flux, sw.kp, t0, t1, targets)
        print(f"Plan {t0.astimezone():%a %H:%M} -> {t1.astimezone():%H:%M} local, "
              f"targets {', '.join(targets) or 'best overall'}, {sw.summary}: {len(picks)} recordings")
        for pk in picks:
            e = pk.entry
            print(f"{pk.when.astimezone():%H:%M} ({pk.when:%H:%M}Z)  {e.khz:>7g}  {pk.pred.score:>3}%  "
                  f"{e.station[:28]:<28} {sched.country_name(e.itu)[:16]:<16} [{pk.target}]")
        return

    if a.cmd == "classify":
        from . import core, logbook
        db = logbook.connect()
        where = "file IS NOT NULL" + ("" if a.all else " AND label IS NULL")
        for r in logbook.rows(db, where, limit=100000):
            print(f"{r['utc_start'][:16]} {r['khz']:g} {r['station']}")
            core.classify_row(r["id"], settings)
        return

    if a.cmd == "report":
        from . import report, logbook
        path = report.write(mission=a.mission, since=a.since, settings=settings)
        print(report.text_summary(report._select(a.mission, a.since)))
        print("\nHTML report:", path)
        return


if __name__ == "__main__":
    main(sys.argv[1:])
