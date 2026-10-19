"""The menu-driven, early-90s style interface (think Turbo Vision / Norton)."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, DataTable, Input, Label, OptionList,
                             ProgressBar, RichLog, Static)
from textual.widgets.option_list import Option

from . import calibration, config, osutil, eibi, logbook, mission, propagation, receiver, report, scan, solar
from .regions import REGIONS

MENUS = {
    "File": [("Update station database & solar data", "update", "Ctrl+U"),
             ("Open recordings folder", "open_folder", ""),
             None,
             ("Exit", "quit", "Alt+X")],
    "Stations": [("Most probable countries", "countries", "F3"),
                 ("Best stations now", "best", "F2"),
                 ("Stations for my night targets", "targets", ""),
                 ("Refresh list", "refresh", "Ctrl+R"),
                 ("Why this score?", "why", "I"),
                 ("Add highlighted country to targets", "add_target", "+"),
                 ("Choose night targets...", "select", "T"),
                 ("Show at another UTC time...", "at_time", "")],
    "Record": [("Record selected station", "record", "F5"),
               ("Record a frequency...", "record_freq", ""),
               ("Play last recording", "play_last", ""),
               ("Stop playback", "stop_play", "")],
    "Night": [("Night mission...", "night", "F6"),
              ("Preview tonight's plan", "plan", "F4"),
              ("Stop running mission", "stop_mission", "")],
    "Log": [("Listening log", "log", "F7"),
            ("Classify pending recordings", "classify", ""),
            ("Generate report", "report", "F8"),
            ("Open HTML report in browser", "open_report", ""),
            ("QSL cards received", "cards", "")],
    "Settings": [("Settings...", "settings", "F9"),
                 ("Setup wizard...", "setup", ""),
                 ("Edit config file", "edit_config", "")],
    "Help": [("Keys & help", "help", "F1"),
             ("About NightDX", "about", "")],
}

CSS = """
Screen { background: #0000aa; color: #ffffff; }
#menubar { height: 1; background: #aaaaaa; color: #000000; }
.menutitle { width: auto; padding: 0 1; background: #aaaaaa; color: #000000; }
.menutitle:hover { background: #00aa00; color: #ffffff; }
#info { height: auto; border: double #55ffff; background: #0000aa; padding: 0 1; color: #ffff55; }
#main { border: double #ffffff; border-title-color: #ffff55; border-title-background: #0000aa;
        background: #0000aa; height: 1fr; }
#mission { height: 4; border: double #55ffff; border-title-color: #55ffff; padding: 0 1; }
#mission ProgressBar { width: 40; }
#mission Bar > .bar--bar { color: #55ff55; background: #000080; }
#fkeys { height: 1; background: #00aaaa; color: #000000; }
DataTable { background: #0000aa; color: #ffffff; scrollbar-background: #000080; scrollbar-color: #55ffff; }
DataTable > .datatable--header { background: #00aaaa; color: #000000; text-style: bold; }
DataTable > .datatable--cursor { background: #00aa00; color: #ffffff; }
DataTable > .datatable--even-row { background: #0000aa; }
DataTable > .datatable--odd-row { background: #00009a; }

/* drop-down menus */
MenuDrop { align: left top; background: transparent; }
MenuDrop > OptionList { background: #aaaaaa; color: #000000; border: solid #000000;
                        width: auto; min-width: 30; height: auto; max-height: 20; }
MenuDrop > OptionList > .option-list--option-highlighted { background: #00aa00; color: #ffffff; }

/* dialogs */
.dialog { background: #aaaaaa; color: #000000; border: double #ffffff;
          border-title-color: #ffffff; border-title-background: #0000aa; border-title-style: bold;
          padding: 1 2; width: 84; max-width: 96vw; height: auto; max-height: 92vh; }
.modal { align: center middle; background: #000040 50%; }
.dialog Label { color: #000000; }
.dialog Input { background: #0000aa; color: #ffff55; border: tall #000080; height: 3; }
.dialog Input:focus { border: tall #ffff55; }
.dialog Checkbox { background: #aaaaaa; color: #000000; border: none; }
.dialog Button { background: #00aa00; color: #ffffff; border: none; height: 1; min-width: 12;
                 margin: 0 1; text-style: bold; }
.dialog Button:focus { background: #55ff55; color: #000000; }
.dialog Button.cancel { background: #aa0000; }
.dialog RichLog { background: #000000; color: #55ff55; height: 12; border: solid #000080; }
.dialog OptionList { background: #0000aa; color: #ffffff; height: 14; border: solid #000080; }
.dialog OptionList > .option-list--option-highlighted { background: #00aa00; color: #ffffff; }
.dialog DataTable { height: 18; }
.buttons { height: 1; margin-top: 1; align: center middle; }
.section { color: #aa0000; text-style: bold; margin-top: 1; }
.row { height: 3; }
#settings_scroll .row { height: 1; margin-bottom: 1; }
#settings_scroll > Label { width: 100%; }
.dialog .hint { color: #333333; }
.dialog Checkbox > .toggle--button { background: #555555; color: #555555; }
.dialog Checkbox.-on > .toggle--button { background: #00aa00; color: #ffffff; text-style: bold; }
#settings_scroll .row Label { padding-top: 0; }
#settings_scroll Input { height: 1; border: none; }
.row Label { width: 26; padding-top: 1; }
.row Input { width: 1fr; }
.cols { height: auto; }
.cols > Vertical { width: 1fr; height: auto; margin: 0 1; }
#settings_scroll { height: auto; max-height: 65vh; }
"""


def hotkey(title: str) -> Text:
    t = Text(title)
    t.stylize("bold #aa0000", 0, 1)
    return t


# =========================================================================== menus

class MenuDrop(ModalScreen):
    BINDINGS = [Binding("escape", "close"), Binding("left", "move(-1)"), Binding("right", "move(1)")]

    def __init__(self, title: str, x: int):
        super().__init__()
        self.title_, self.x = title, x

    def compose(self) -> ComposeResult:
        opts = []
        for item in MENUS[self.title_]:
            if item is None:
                opts.append(None)
            else:
                label, action, key = item
                opts.append(Option(f"{label:<38}{key:>7}", id=action))
        ol = OptionList(*opts)
        ol.styles.offset = (self.x, 1)
        yield ol

    def on_click(self, event) -> None:
        if event.widget is self:
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_move(self, d: int) -> None:
        self.dismiss(("move", d))

    @on(OptionList.OptionSelected)
    def chosen(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(("do", ev.option.id))


class MenuTitle(Static):
    def __init__(self, title: str):
        super().__init__(hotkey(title), classes="menutitle")
        self.menu = title

    def on_click(self) -> None:
        self.app.open_menu(self.menu)


# =========================================================================== dialogs

class Dialog(ModalScreen):
    DEFAULT_CLASSES = "modal"
    BINDINGS = [Binding("escape", "close")]

    def action_close(self) -> None:
        self.dismiss(None)


class MessageBox(Dialog):
    def __init__(self, title: str, text: str):
        super().__init__()
        self.t, self.text = title, text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog") as d:
            d.border_title = f" {self.t} "
            with VerticalScroll(id="msgscroll"):
                yield Static(self.text)
            with Horizontal(classes="buttons"):
                yield Button("OK", id="ok")

    @on(Button.Pressed)
    def ok(self) -> None:
        self.dismiss(None)


class Prompt(Dialog):
    def __init__(self, title: str, label: str, value: str = ""):
        super().__init__()
        self.t, self.label, self.value = title, label, value

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog") as d:
            d.border_title = f" {self.t} "
            yield Label(self.label)
            yield Input(self.value, id="val")
            with Horizontal(classes="buttons"):
                yield Button("OK", id="ok")
                yield Button("Cancel", id="cancel", classes="cancel")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        self.dismiss(self.query_one("#val", Input).value)

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class SelectTargets(Dialog):
    """Pick regions / countries / free text, e.g. "India, Africa, Belgium"."""

    def __init__(self, sched: eibi.Schedule, current: list[str]):
        super().__init__()
        self.sched, self.current = sched, current

    def compose(self) -> ComposeResult:
        counts: dict[str, int] = {}
        for e in self.sched.entries:
            counts[e.region] = counts.get(e.region, 0) + 1
        with Vertical(classes="dialog") as d:
            d.border_title = " Choose what to listen to "
            yield Label("Targets (comma separated). Enter on a list adds it. Free text matches "
                        "station names;\nprefixes: lang:Hindi  target:WAf  tx:ASC  station:BBC")
            yield Input(", ".join(self.current), id="targets")
            with Horizontal(classes="cols"):
                with Vertical():
                    yield Label("Regions")
                    yield OptionList(*[Option(f"{r} ({counts.get(r, 0)})", id=r)
                                       for r in REGIONS if counts.get(r)], id="regions")
                with Vertical():
                    yield Label("Countries (broadcaster)")
                    yield OptionList(*[Option(f"{name[:30]} [{c}] ({n})", id=c)
                                       for c, name, n in self.sched.countries_in_use()],
                                     id="countries")
            with Horizontal(classes="buttons"):
                yield Button("OK", id="ok")
                yield Button("Clear", id="clear")
                yield Button("Cancel", id="cancel", classes="cancel")

    @on(OptionList.OptionSelected)
    def add(self, ev: OptionList.OptionSelected) -> None:
        inp = self.query_one("#targets", Input)
        val = ev.option.id
        if ev.option_list.id == "countries":
            val = self.sched.country_name(val).split(" (")[0]
        items = [x.strip() for x in inp.value.split(",") if x.strip()]
        if val not in items:
            items.append(val)
        inp.value = ", ".join(items)

    @on(Button.Pressed, "#clear")
    def clear(self) -> None:
        self.query_one("#targets", Input).value = ""

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        self.dismiss([x.strip() for x in self.query_one("#targets", Input).value.split(",") if x.strip()])

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class RecordDialog(Dialog):
    """Records one entry in a worker thread, showing progress and the AI verdict."""

    def __init__(self, entry: eibi.Entry):
        super().__init__()
        self.entry = entry
        self.stop = threading.Event()      # cancel: discard
        self.finish = threading.Event()    # stop now: keep what was recorded

    def compose(self) -> ComposeResult:
        app: NightDX = self.app
        e = self.entry
        with Vertical(classes="dialog") as d:
            d.border_title = f" Recording {e.khz:g} kHz "
            yield Label(f"{e.station} - {app.sched.country_name(e.itu)} - "
                        f"{app.sched.language_name(e.lang)} - target {e.target}\n"
                        f"Duration {app.settings.recording.duration_s}s")
            yield ProgressBar(total=1.0, show_eta=False, id="pb")
            yield RichLog(id="rlog", wrap=True, markup=False)
            yield Label("Stop now: end early and keep the audio.  Esc/Close: back to the list.",
                        id="rhint")
            with Horizontal(classes="buttons"):
                yield Button("Stop now", id="stopnow")
                yield Button("Play", id="play", disabled=True)
                yield Button("Cancel", id="close", classes="cancel")

    def on_mount(self) -> None:
        self.app.radio_busy = True
        self.recording = True
        self.row: dict = {}
        self._watch = None
        self.go()

    def _ui(self, fn, *args, **kw) -> None:
        """Run fn on the UI thread, unless the dialog has already been closed."""
        def safe():
            if self.is_attached:
                fn(*args, **kw)
        self.app.call_from_thread(safe)

    @work(thread=True)
    def go(self) -> None:
        from . import core
        app: NightDX = self.app
        say = lambda m: self._ui(lambda: self.query_one(RichLog).write(m))
        prog = lambda f: self._ui(lambda: self.query_one(ProgressBar).update(progress=f))
        try:
            self.row = core.record_entry(self.entry, app.sched, app.settings, app.sw.flux,
                                         app.sw.kp, progress=prog, stop=self.stop,
                                         finish=self.finish, log=say)
        finally:
            app.radio_busy = False
        if self.row.get("file"):
            app.last_file = self.row["file"]
        self._ui(self._finished)

    def _finished(self) -> None:
        self.recording = False
        self.query_one(ProgressBar).update(progress=1.0)
        self.query_one("#close", Button).label = "Close"
        stop = self.query_one("#stopnow", Button)
        stop.label = "Stop"
        stop.disabled = True
        self.query_one("#rhint", Label).update("Play to listen, Stop to stop playback, "
                                               "Esc/Close to go back to the list.")
        if self.row.get("file"):
            play = self.query_one("#play", Button)
            play.disabled = False
            play.focus()

    def _show_playing(self, playing: bool) -> None:
        """Play is greyed out while the recording plays; Stop only then."""
        if self.recording or not self.is_attached:
            return
        self.query_one("#play", Button).disabled = playing or not self.row.get("file")
        stop = self.query_one("#stopnow", Button)
        stop.disabled = not playing
        if not playing and self._watch:
            self._watch.stop()
            self._watch = None

    def _check_playing(self) -> None:
        self._show_playing(self.app.player.is_playing())

    @on(Button.Pressed, "#stopnow")
    def stop_now(self) -> None:
        if self.recording:
            self.finish.set()
            self.query_one("#stopnow", Button).disabled = True
            self.query_one(RichLog).write("  stopping early - saving what we have...")
        else:
            self.app.action_stop_play()
            self._show_playing(False)

    @on(Button.Pressed, "#play")
    def play(self) -> None:
        self.app.play(self.row["file"])
        self._show_playing(True)
        if self.app.player.is_playing():          # re-enable Play when it ends by itself
            self._watch = self.set_interval(0.5, self._check_playing)
        else:                                     # opened in an external player
            self._show_playing(False)

    @on(Button.Pressed, "#close")
    def close_(self) -> None:
        if self.recording:
            self.stop.set()          # discard the partial recording
        self.app.action_stop_play()
        self.dismiss(None)

    def action_close(self) -> None:
        self.close_()


class NightDialog(Dialog):
    def compose(self) -> ComposeResult:
        m = self.app.settings.mission
        with Vertical(classes="dialog") as d:
            d.border_title = " Night mission "
            yield Label("Records unattended between the two local times, cycling through the\n"
                        "targets and picking whatever propagation favours. The computer is kept awake;\n"
                        "the mission keeps running if you quit NightDX.")
            with Horizontal(classes="row"):
                yield Label("Start (local HH:MM)")
                yield Input(m.start, id="start")
            with Horizontal(classes="row"):
                yield Label("End (local HH:MM)")
                yield Input(m.end, id="end")
            with Horizontal(classes="row"):
                yield Label("Targets (empty=best)")
                yield Input(", ".join(m.targets), id="targets",
                            placeholder="e.g. India, Africa, lang:Hindi - empty: best overall")
            st = mission.read_state()
            yield Label(f"Current mission: {st.get('state')}  {st.get('status', '')}", id="mstate")
            with Horizontal(classes="buttons"):
                yield Button("Preview", id="preview")
                yield Button("Arm tonight", id="arm")
                yield Button("Start now", id="now")
                yield Button("Stop", id="stopm", classes="cancel")
                yield Button("Close", id="close", classes="cancel")

    def _values(self):
        g = lambda i: self.query_one(f"#{i}", Input).value.strip()
        targets = [t.strip() for t in g("targets").split(",") if t.strip()]
        s = self.app.settings
        s.mission.start, s.mission.end, s.mission.targets = g("start"), g("end"), targets
        s.save()
        return g("start"), g("end"), targets

    @on(Button.Pressed, "#preview")
    def preview(self) -> None:
        self._values()
        self.dismiss("plan")

    @on(Button.Pressed, "#arm")
    @on(Button.Pressed, "#now")
    def start(self, ev: Button.Pressed) -> None:
        start, end, targets = self._values()
        if mission.read_state().get("state") in ("waiting", "recording", "scanning"):
            self.app.notify("A mission is already running - stop it first.", severity="error")
            return
        pid = mission.launch_background(targets, start, end, now=ev.button.id == "now")
        self.app.notify(f"Mission started (pid {pid}). Log: {config.HOME / 'mission.log'}")
        self.dismiss(None)

    @on(Button.Pressed, "#stopm")
    def stopm(self) -> None:
        self.app.notify("Mission stopped." if mission.stop_background() else "No mission running.")
        self.dismiss(None)

    @on(Button.Pressed, "#close")
    def close_(self) -> None:
        self.dismiss(None)


class LogDialog(Dialog):
    BINDINGS = [Binding("escape", "close"), Binding("p", "play", "Play"),
                Binding("s", "stop", "Stop"), Binding("c", "classify", "Classify")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog") as d:
            d.border_title = " Listening log "
            d.styles.width = 120
            yield Label("Enter/P play   S stop   C re-classify   Esc close")
            yield DataTable(id="logt", cursor_type="row", zebra_stripes=True)
            yield Static(id="logsum")
            with Horizontal(classes="buttons"):
                yield Button("Play", id="play")
                yield Button("Report", id="report")
                yield Button("Close", id="close", classes="cancel")

    def on_mount(self) -> None:
        t = self.query_one(DataTable)
        t.add_columns("UTC", "kHz", "Station", "Country", "Lang", "Pred", "Mod dB", "AI verdict", "Mission")
        self.refresh_rows()
        t.focus()

    def refresh_rows(self) -> None:
        t = self.query_one(DataTable)
        t.clear()
        self.rows = {str(r["id"]): r for r in logbook.rows(logbook.connect(), limit=500)}
        colors = {"voice": "bold #55ff55", "music": "bold #ff55ff", "voice+music": "bold #ffff55",
                  "noise": "#aaaaaa"}
        for k, r in self.rows.items():
            verdict = Text(r["error"][:24], style="#ff5555") if r["error"] else Text(
                f"{r['label'] or 'pending'} {r['voice_pct'] or 0:.0f}/{r['music_pct'] or 0:.0f}/{r['noise_pct'] or 0:.0f}",
                style=colors.get(r["label"], ""))
            t.add_row(r["utc_start"][5:16].replace("T", " "), f"{r['khz']:g}", (r["station"] or "")[:24],
                      (r["country"] or "")[:14], (r["language"] or "")[:10], f"{r['predicted_score'] or 0}%",
                      "" if r["modulation_db"] is None else f"{r['modulation_db']:.0f}",
                      verdict, r["mission"] or "", key=k)
        n = len(self.rows)
        self.query_one("#logsum", Static).update(f"{n} recordings in the log (voice/music/noise %)")

    def _current(self):
        t = self.query_one(DataTable)
        if not self.rows:
            return None
        key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        return self.rows.get(key)

    @on(DataTable.RowSelected)
    @on(Button.Pressed, "#play")
    def action_play(self) -> None:
        r = self._current()
        if r and r["file"]:
            self.app.play(r["file"])

    def action_stop(self) -> None:
        self.app.action_stop_play()

    @work(thread=True)
    def action_classify(self) -> None:
        from . import core
        r = self._current()
        if r and r["file"]:
            self.app.call_from_thread(self.app.notify, "Classifying...")
            core.classify_row(r["id"], self.app.settings, log=lambda m: None)
            self.app.call_from_thread(self.refresh_rows)

    @on(Button.Pressed, "#report")
    def rep(self) -> None:
        self.dismiss("report")

    @on(Button.Pressed, "#close")
    def close_(self) -> None:
        self.dismiss(None)


class SetupWizard(Dialog):
    """First-run setup: location, bands, radio, optional features, folders, QSL details."""

    def __init__(self, first_run: bool = False):
        super().__init__()
        self.first_run = first_run

    def _row(self, label: str, wid: str, value, placeholder: str = "") -> ComposeResult:
        with Horizontal(classes="row"):
            yield Label(label)
            yield Input(str(value), id=wid, placeholder=placeholder, compact=True)

    def compose(self) -> ComposeResult:
        from . import extras
        s = self.app.settings
        with Vertical(classes="dialog") as d:
            d.border_title = " NightDX setup "
            d.styles.width = 100
            with VerticalScroll(id="settings_scroll"):
                yield Label("Welcome! A few questions and NightDX is ready. You can run this again "
                            "any time from Settings > Setup wizard.")
                yield Label("[ 1. WHERE YOU LISTEN ]", classes="section")
                yield Label("Coordinates in decimal degrees (south/west negative). Tip: right-click "
                            "your place in Google Maps to copy them.")
                yield from self._row("Place", "w_name", s.location.name, "e.g. Trieste")
                yield from self._row("Country", "w_country", s.location.country, "e.g. Italy")
                yield from self._row("Latitude", "w_lat", s.location.lat or "", "e.g. 45.65")
                yield from self._row("Longitude", "w_lon", s.location.lon or "", "e.g. 13.78")
                yield Label("[ 2. BANDS ]", classes="section")
                yield Checkbox("Include medium wave (520-1710 kHz) as well as shortwave",
                               s.stations.medium_wave, id="w_mw")
                yield Label("[ 3. RADIO ]", classes="section")
                yield Label("checking for the AirSpy HF+...", id="w_radio")
                yield Button("Check again", id="w_check")
                yield Label("[ 4. OPTIONAL FEATURES ]", classes="section")
                for key, f in extras.FEATURES.items():
                    have = extras.installed(key)
                    yield Checkbox(f["title"] + ("  (installed)" if have else ""), have,
                                   id=f"w_x_{key}", disabled=have)
                    yield Label(f"      {f['size']}", classes="hint")
                yield Button("Install selected", id="w_install")
                yield RichLog(id="w_log", wrap=True, markup=False)
                yield Label("[ 5. RECORDINGS ]", classes="section")
                yield from self._row("Folder", "w_rec", s.recording.output_dir)
                yield from self._row("Length (seconds)", "w_dur", s.recording.duration_s)
                yield Label("[ 6. RECEPTION REPORTS (optional) ]", classes="section")
                yield from self._row("Your name", "w_qname", s.qsl.name)
                yield from self._row("Your e-mail", "w_qmail", s.qsl.email)
                yield from self._row("Postal address", "w_qpost", s.qsl.postal_address,
                                     "lines separated by /  (for QSL cards)")
                yield from self._row("Receiver", "w_qrx", s.qsl.receiver)
                yield from self._row("Antenna", "w_qant", s.qsl.antenna, "e.g. 20 m long wire")
            with Horizontal(classes="buttons"):
                yield Button("Save", id="w_save")
                yield Button("Later" if self.first_run else "Cancel", id="w_cancel", classes="cancel")

    def on_mount(self) -> None:
        self.query_one("#w_log", RichLog).display = False
        self.check_radio()

    @work(thread=True)
    def check_radio(self) -> None:
        from .osutil import driver_hint
        info = receiver.device_info(self.app.settings)
        ok = info.startswith("AirSpy")
        text = f"Found: {info}" if ok else f"The HF+ was not found: {info}\n{driver_hint()}"
        self.app.call_from_thread(self.query_one("#w_radio", Label).update, text)

    @on(Button.Pressed, "#w_check")
    def recheck(self) -> None:
        self.query_one("#w_radio", Label).update("checking...")
        self.check_radio()

    @on(Button.Pressed, "#w_install")
    def install(self) -> None:
        from . import extras
        chosen = [k for k in extras.FEATURES
                  if self.query_one(f"#w_x_{k}", Checkbox).value and not extras.installed(k)]
        if not chosen:
            self.app.notify("Nothing to install - tick a feature first.")
            return
        log = self.query_one("#w_log", RichLog)
        log.display = True
        self.query_one("#w_install", Button).disabled = True
        self._install(chosen)

    @work(thread=True)
    def _install(self, chosen: list[str]) -> None:
        from . import extras
        say = lambda m: self.app.call_from_thread(self.query_one("#w_log", RichLog).write, m)
        ok = extras.install(chosen, say)

        def done():
            self.query_one("#w_install", Button).disabled = False
            for k in chosen:
                cb = self.query_one(f"#w_x_{k}", Checkbox)
                if extras.installed(k):
                    cb.label = f"{extras.FEATURES[k]['title']}  (installed)"
                    cb.disabled = True
            self.app.notify("Installed - restart NightDX to use the new features." if ok
                            else "Installation failed - see the log above.",
                            severity="information" if ok else "error")
        self.app.call_from_thread(done)

    @on(Button.Pressed, "#w_save")
    def save(self) -> None:
        g = lambda i: self.query_one(f"#{i}", Input).value.strip()
        try:
            lat, lon = float(g("w_lat").replace(",", ".")), float(g("w_lon").replace(",", "."))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
        except ValueError:
            self.app.notify("Please enter latitude and longitude as decimal numbers, "
                            "e.g. 45.65 and 13.78.", severity="error")
            return
        s = self.app.settings
        s.location.name, s.location.country, s.location.lat, s.location.lon = (
            g("w_name") or "My QTH", g("w_country"), lat, lon)
        s.stations.medium_wave = self.query_one("#w_mw", Checkbox).value
        s.recording.output_dir = g("w_rec") or s.recording.output_dir
        try:
            s.recording.duration_s = max(10, int(g("w_dur")))
        except ValueError:
            pass
        s.qsl.name, s.qsl.email, s.qsl.postal_address = g("w_qname"), g("w_qmail"), g("w_qpost")
        s.qsl.receiver, s.qsl.antenna = g("w_qrx") or s.qsl.receiver, g("w_qant") or s.qsl.antenna
        s.general.setup_done = True
        s.save()
        self.dismiss("saved")

    @on(Button.Pressed, "#w_cancel")
    def cancel(self) -> None:
        self.dismiss(None)


class SettingsDialog(Dialog):
    LABELS = {"lat": "Latitude (+N)", "lon": "Longitude (+E)", "duration_s": "Recording length (s)",
              "airspyhf_rx": "airspyhf_rx command", "tx_power_kw": "Assumed TX power (kW)",
              "noise_environment": "Noise (quiet/rural/residential/city)",
              "backend": "AI backend (auto/ast/dsp)", "format": "File format (flac/wav)",
              "targets": "Default targets", "qsl.name": "Your name",
              "qsl.email": "Your e-mail (signature)", "qsl.gmail_account": "Gmail account (optional)",
              "qsl.postal_address": "Postal address (a / b / c)",
              "qsl.years_collecting": "Years collecting QSLs", "qsl.receiver": "Receiver",
              "qsl.antenna": "Antenna", "mission.discovery": "Discovery: prefer stations I miss",
              "mission.discovery_min_snr": "Discovery: min. clarity (dB)",
              "mission.rest_days": "Discovery: 'new' again after (days)"}

    def compose(self) -> ComposeResult:
        s = self.app.settings
        with Vertical(classes="dialog") as d:
            d.border_title = " Settings "
            d.styles.width = 96
            with VerticalScroll(id="settings_scroll"):
                for sec in fields(s):
                    yield Label(f"[ {sec.name.upper()} ]", classes="section")
                    obj = getattr(s, sec.name)
                    for f in fields(obj):
                        v = getattr(obj, f.name)
                        wid = f"{sec.name}__{f.name}"
                        label = self.LABELS.get(f"{sec.name}.{f.name}",
                                                self.LABELS.get(f.name, f.name.replace("_", " ").capitalize()))
                        if isinstance(v, bool):
                            yield Checkbox(label, v, id=wid)
                        else:
                            with Horizontal(classes="row"):
                                yield Label(label)
                                yield Input(", ".join(v) if isinstance(v, list) else str(v), id=wid,
                                            compact=True)
            with Horizontal(classes="buttons"):
                yield Button("Save", id="save")
                yield Button("Cancel", id="cancel", classes="cancel")

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        s = self.app.settings
        try:
            for sec in fields(s):
                obj = getattr(s, sec.name)
                for f in fields(obj):
                    w = self.query_one(f"#{sec.name}__{f.name}")
                    old = getattr(obj, f.name)
                    if isinstance(old, bool):
                        val = w.value
                    elif isinstance(old, list):
                        val = [x.strip() for x in w.value.split(",") if x.strip()]
                    else:
                        val = type(old)(w.value.strip())
                    setattr(obj, f.name, val)
        except ValueError as e:
            self.app.notify(f"Invalid value: {e}", severity="error")
            return
        s.save()
        self.dismiss("saved")

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.dismiss(None)


HELP = """NightDX - unattended shortwave listening with the AirSpy HF+

  F1  this help              F6  night mission dialog
  F2  best stations now      F7  listening log (play, re-classify)
  F3  most probable countries F8 generate report
  F4  preview the night      F9  settings
  F5  record highlighted     F10 menu bar (arrows to move, Enter to pick)
  Enter  countries: show that country's stations / stations: record
  Esc    back (stations -> countries), also stops playback
  I      why this score?     +  add country to night targets
  T      choose night targets
  Ctrl+R refresh the list (it also refreshes by itself every minute)
  Ctrl+U download a fresh EiBi schedule + space weather
  Alt+letter opens a menu (macOS Terminal: enable "Use Option as Meta key")
  Alt+X / Ctrl+Q exit

The Score (0-100 %)
  The estimated chance that you can comfortably listen to the station
  right now from your location. It multiplies two probabilities:
   * P(MUF): the frequency is low enough to be reflected by the
     ionosphere all along the path (otherwise it skips past you);
   * P(SNR): the signal is at least 15 dB above the local noise,
     after path loss, daytime D-layer absorption and the direction of
     the transmitting antenna.
  Both include day-to-day variability, so ~50 % means "a coin flip".
  It is a model estimate (powers are assumed: EiBi doesn't list them),
  good for ranking, not a promise. Press I on a row for the details.

Heard dB (measured)
  At startup and on Ctrl+R the HF+ snapshots every band (about 10 s)
  and measures the carrier on each scheduled frequency: dB above the
  noise in a 5 kHz channel. 20+ = solid, 5-20 = audible, "-" = nothing
  there, "?" = not scanned yet. "*" = another station on the same
  frequency is the likelier source. Lists put heard stations first,
  then the rest by prediction. Each scan also teaches the model how
  strong each transmitter really is at your location (press I).

How it works
  1. File > Update downloads the EiBi schedule and NOAA space weather.
  2. The propagation model scores every broadcast on the air for your
     location (Settings) - 0-100, like VOACAP's reliability.
  3. F3 shows which countries are most likely heard now. Night targets
     (T) can be regions (Africa), countries (India), languages
     (lang:Hindi), target areas (target:WAf) or station names; with no
     targets the night mission simply records the best stations.
  4. F5 records the highlighted station; files go to
     your recordings folder (<date>/...) as FLAC with full station metadata
     (plus a .json sidecar).
  5. An AudioSet transformer classifies each recording as voice, music,
     voice+music or noise.
  6. Night > Night mission arms an unattended run (e.g. 23:30-06:30);
     in the morning open Log > Open HTML report and listen.

Medium wave (this version)
  MW stations come from EiBi (international MW only) and, much more
  completely, from MWLIST. To add MWLIST: register (free) at fmlist.org,
  log in at fmscan.org, set your location, then Tools > "Userlist for
  Perseus / ELAD / Winradio / SDR Console": choose LW/MW/SW (0-30 MHz),
  CSV, separator Tab. Put the file in the 'mwlist' folder of the data
  folder ({home}) and press
  Ctrl+U. MW is mainly a night band: by day only stations within a few
  hundred km arrive (ground wave); after dark the sky wave brings in
  strong stations 1000-2500 km away.

Close SDR++/SDR Console etc. before recording: only one program can use
the HF+ at a time."""


# =========================================================================== app

class NightDX(App):
    force_setup = False
    CSS = CSS
    TITLE = "NightDX"
    BINDINGS = [
        Binding("f1", "do('help')"), Binding("f2", "do('best')"), Binding("f3", "do('countries')"),
        Binding("ctrl+r", "do('refresh')"), Binding("i", "do('why')"), Binding("t", "do('select')"),
        Binding("plus", "do('add_target')"), Binding("escape", "do('back')"),
        Binding("backspace", "do('back')"),
        Binding("f4", "do('plan')"), Binding("f5", "do('record')"), Binding("f6", "do('night')"),
        Binding("f7", "do('log')"), Binding("f8", "do('report')"), Binding("f9", "do('settings')"),
        Binding("f10", "menu('File')"), Binding("ctrl+u", "do('update')"),
        Binding("alt+x", "quit"), Binding("ctrl+q", "quit"),
        *[Binding(f"alt+{m[0].lower()}", f"menu('{m}')") for m in MENUS],
    ]

    def __init__(self):
        super().__init__()
        self.settings = config.load()
        self.sched: eibi.Schedule | None = None
        self.sw = solar.SpaceWeather()
        self.view = "countries"
        self.view_time: datetime | None = None
        self.rows: dict[str, eibi.Entry] = {}
        self.preds: dict[str, propagation.Prediction] = {}
        self.meas: dict[str, float | None] = {}
        self.scanning = ""              # progress text while a band scan runs
        self.radio_busy = False         # a recording dialog owns the HF+
        self._minute = -1
        self.player = osutil.Player()
        self.last_file: str | None = None
        self.device = "checking..."

    # ---------------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        with Horizontal(id="menubar"):
            yield Static(" ≡ ", classes="menutitle")
            for m in MENUS:
                yield MenuTitle(m)
        yield Static(id="info")
        with Container(id="main"):
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        with Vertical(id="mission"):
            yield Static(id="mline")
            yield ProgressBar(total=1.0, show_eta=False, id="mprog")
        yield Static(Text.from_markup(
            "[b]F1[/]Help [b]F2[/]Stns [b]F3[/]Ctry [b]F4[/]Plan [b]F5[/]Rec [b]F6[/]Night "
            "[b]F7[/]Log [b]F8[/]Rep [b]F9[/]Set [b]F10[/]Menu [b]^R[/]Refr [b]I[/]nfo"), id="fkeys")

    def on_mount(self) -> None:
        self.query_one("#mission").border_title = "Night mission"
        from . import server
        err = server.start(self.settings)       # report page + "Send report" buttons
        if err:
            self.notify(err, severity="warning")
        self.set_interval(1, self.tick)
        self.tick()
        if not self.settings.general.setup_done or self.force_setup:
            self.push_screen(SetupWizard(first_run=True), self._after_setup)
        else:
            self.startup()

    def _after_setup(self, result) -> None:
        self.settings = config.load()
        if result == "saved":
            self.notify("Setup saved. Welcome to NightDX!")
        self.startup()

    @work(thread=True)
    def startup(self) -> None:
        self.device = receiver.device_info(self.settings)
        if not eibi.status():
            self.call_from_thread(self.notify, "First run: downloading the EiBi schedule...")
            try:
                eibi.download(self.settings.stations.season)
            except Exception as e:
                self.call_from_thread(self.notify, f"Download failed: {e}", severity="error")
                return
        self.sw = solar.current()
        self.sched = eibi.Schedule.load(self.settings)
        self.call_from_thread(self.render_view)
        self.call_from_thread(self.run_scan)

    # ---------------------------------------------------------------- band scan
    def _radio_free(self) -> bool:
        if self.radio_busy:
            return False
        st = mission.read_state()
        return st.get("state") in (None, "idle", "done", "stopped", "failed", "waiting")

    @work(thread=True, exclusive=True, group="scan")
    def run_scan(self) -> None:
        if not self.sched:
            return
        if not self._radio_free():
            self.call_from_thread(self.notify, "Radio busy (recording / night mission) - "
                                  "showing the latest scan.", timeout=4)
            return

        def prog(i, n, mhz):
            self.scanning = f"scanning {mhz:.2f} MHz {i + 1}/{n}..."
            self.call_from_thread(self._title_progress)
        try:
            r = scan.survey(self.sched, self.settings, self.sw.flux, self.sw.kp, progress=prog)
        except Exception as e:
            self.scanning = ""
            self.call_from_thread(self.notify, f"Scan failed: {e}", severity="error")
            self.call_from_thread(self.render_view)
            return
        self.scanning = ""
        self.call_from_thread(self.render_view)
        self.call_from_thread(self.notify, f"Scan: {r['heard']} of {r['scanned']} frequencies heard "
                              f"({r['seconds']:.0f}s). Model learned from {r['learned']} stations.",
                              timeout=6)

    def _title_progress(self) -> None:
        main = self.query_one("#main")
        title = str(main.border_title or "")
        base = title.split(" [")[0]
        main.border_title = f"{base} [{self.scanning}] "

    # ---------------------------------------------------------------- status
    def tick(self) -> None:
        now = datetime.now(timezone.utc)
        st = eibi.status()
        loc = self.settings.location
        src = "EiBi+AOKI" if self.sched and getattr(self.sched, "aoki_status", "") else "EiBi"
        db = f"{src} {st['season']} ({len(self.sched.entries) if self.sched else '...'} bc)" if st else "EiBi: none"
        self.query_one("#info", Static).update(Text(
            f"{loc.name} {loc.lat:.2f}N {loc.lon:.2f}E  UTC {now:%H:%M:%S}  local "
            f"{datetime.now():%H:%M}  {self.sw.summary}\n"
            f"{db}  Radio: {self.device}\n"
            f"Night targets: {', '.join(self.settings.mission.targets) or 'none (best overall)'}"
            f"   {self.sched.mw_status if self.sched else ''}"))
        # live lists follow the clock: refresh once a minute while nothing is open on top
        if now.minute != self._minute:
            self._minute = now.minute
            if (self.sched and not self.view_time and self.view != "plan"
                    and len(self.screen_stack) == 1):
                self.render_view()
        ms = mission.read_state()
        state = ms.get("state", "idle")
        line = f"{state.upper()}: {ms.get('status') or 'no mission running - F6 to arm one'}"
        if ms.get("mission"):
            line += f"   {ms['mission']}  recordings: {ms.get('count', 0)}"
        if ms.get("lines"):
            line += "\n" + ms["lines"][-1]
        self.query_one("#mline", Static).update(Text(line))
        self.query_one("#mprog", ProgressBar).update(
            progress=ms.get("progress", 0) if state == "recording" else 0)

    # ---------------------------------------------------------------- main table
    def _table(self, title: str, columns: list[str]) -> DataTable:
        t = self.query_one("#table", DataTable)
        t.clear(columns=True)
        t.add_columns(*columns)
        self.query_one("#main").border_title = f" {title} "
        self.rows, self.preds, self.meas = {}, {}, {}
        return t

    @staticmethod
    def _score(score: int) -> Text:
        return Text(f"{score:>3}%", style="bold #55ff55" if score >= 70 else
                    "bold #ffff55" if score >= 30 else "#ff5555")

    @staticmethod
    def _heard(m: float | None, primary: bool = True) -> Text:
        if m is None:
            return Text("  ?", style="#8888cc")             # not scanned
        if m == float("-inf"):
            return Text("  -", style="#ff5555")             # scanned, nothing there
        style = "bold #55ff55" if m >= 20 else "bold #ffff55" if m >= 5 else "#ffffff"
        return Text(f"{max(m, 0):>3.0f}" + ("" if primary else "*"), style=style if primary else "#aaaaaa")

    def _ranked(self, pred=None):
        """[(entry, prediction, measured, primary)] on the air at the view time, best first.
        Measurements are used only for 'now' views."""
        t_utc = self.view_time or datetime.now(timezone.utc)
        ents = self.sched.on_air(t_utc, self.settings.recording.duration_s / 60)
        if pred:
            ents = [e for e in ents if pred(e)]
        pairs, seen = [], set()
        for e, p in propagation.rank(ents, self.settings, t_utc, self.sw.flux, self.sw.kp):
            if e.key not in seen:
                seen.add(e.key)
                pairs.append((e, p))
        result = None if self.view_time else scan.load()
        when = "now" if not self.view_time else f"at {t_utc:%H:%M} UTC"
        return when, scan.order(pairs, result), result is not None

    def render_view(self) -> None:
        """Redraw the current view, keeping the cursor where it was."""
        if not self.sched:
            return
        t = self.query_one("#table", DataTable)
        row = t.cursor_row or 0
        if self.view == "countries":
            self.show_countries()
        elif self.view == "plan":
            self.show_plan()
        else:
            self.show_stations(self.view)
        if t.row_count:
            t.move_cursor(row=min(row, t.row_count - 1))

    def _title(self, text: str, measured: bool) -> None:
        src = "measured + predicted" if measured else "predicted only - Ctrl+R to scan"
        if self.scanning:
            src = self.scanning
        self.query_one("#main").border_title = f" {text} [{src}] "

    def show_countries(self) -> None:
        self.view = "countries"
        when, rows, measured = self._ranked()
        best: dict[str, dict] = {}
        for e, p, m, primary in rows:                 # rows are best first
            c = best.setdefault(e.itu, {"e": e, "p": p, "m": m, "n": 0, "heard": 0, "pmax": 0})
            c["n"] += 1
            c["pmax"] = max(c["pmax"], p.score)
            c["heard"] += scan.heard(m) and primary
        order = sorted(best.items(), key=lambda kv: (
            -(kv[1]["m"] if scan.heard(kv[1]["m"]) else -999), -kv[1]["pmax"], -kv[1]["n"]))
        t = self._table("", ["Country", "Region", "Heard dB", "Pred", "Heard", "On air",
                             "Best station", "kHz", "Language"])
        self._title(f"Countries {when} - Enter: stations  +: add to night", measured)
        for itu, c in order:
            e, p = c["e"], c["p"]
            t.add_row(self.sched.country_name(itu)[:22], e.region[:13], self._heard(c["m"]),
                      self._score(c["pmax"]), f"{c['heard']}" if measured else "?", str(c["n"]),
                      e.station[:22], f"{e.khz:g}", self.sched.language_name(e.lang)[:12], key=itu)
            self.rows[itu], self.preds[itu], self.meas[itu] = e, p, c["m"]
        t.focus()

    def show_stations(self, view: str) -> None:
        self.view = view
        if view.startswith("country:"):
            itu = view.split(":", 1)[1]
            pred, what = (lambda e: e.itu == itu), f"{self.sched.country_name(itu)} (Esc: back)"
        elif view == "targets" and self.settings.mission.targets:
            ms = [self.sched.matcher(x) for x in self.settings.mission.targets]
            pred, what = (lambda e: any(m(e) for m in ms)), ", ".join(self.settings.mission.targets)
        else:
            pred, what = None, "all stations"
        when, rows, measured = self._ranked(pred)
        t = self._table("", ["kHz", "Heard dB", "Pred", "Station", "Country", "Language",
                             "Target", "km", "Sched UTC", "Days", "Note"])
        self._title(f"On the air {when} - {what}", measured)
        for i, (e, p, m, primary) in enumerate(rows):
            note = p.note if primary or not scan.heard(m) else "shares freq."
            t.add_row(f"{e.khz:g}", self._heard(m, primary), self._score(p.score), e.station[:26],
                      self.sched.country_name(e.itu)[:16], self.sched.language_name(e.lang)[:12],
                      e.target, f"{p.distance_km:.0f}", e.time_str, e.days or "daily", note,
                      key=str(i))
            self.rows[str(i)], self.preds[str(i)], self.meas[str(i)] = e, p, m
        if not rows:
            self.notify("Nothing matching is on the air at this time.")
        t.focus()

    def show_plan(self) -> None:
        if not self.sched:
            return
        m = self.settings.mission
        t0, t1 = mission.window(m.start, m.end)
        picks = mission.preview(self.sched, self.settings, self.sw.flux, self.sw.kp, t0, t1, m.targets)
        t = self._table(f"Night plan {t0.astimezone():%a %H:%M}-{t1.astimezone():%H:%M} local: "
                        f"{len(picks)} recordings - {', '.join(m.targets) or mission.ANY}",
                        ["Local", "UTC", "kHz", "Score", "Station", "Country", "Language", "Target"])
        for pk in picks:
            e = pk.entry
            t.add_row(f"{pk.when.astimezone():%H:%M}", f"{pk.when:%H:%M}", f"{e.khz:g}",
                      f"{pk.pred.score}%", e.station[:28], self.sched.country_name(e.itu)[:18],
                      self.sched.language_name(e.lang)[:14], pk.target, key=str(len(self.rows)))
            self.rows[str(len(self.rows))] = e
        self.view = "plan"

    def selected_key(self) -> str | None:
        t = self.query_one("#table", DataTable)
        if not self.rows or t.cursor_row is None:
            return None
        return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value

    def selected_entry(self) -> eibi.Entry | None:
        return self.rows.get(self.selected_key() or "")

    @on(DataTable.RowSelected, "#table")
    def row_enter(self) -> None:
        if self.view == "countries":
            itu = self.selected_key()
            if itu:
                self.show_stations(f"country:{itu}")
        else:
            self.action_do("record")

    # ---------------------------------------------------------------- menus
    def open_menu(self, title: str) -> None:
        x = 5                      # width of the " = " box before "File"
        for m in MENUS:
            if m == title:
                break
            x += len(m) + 2

        def after(res):
            if not res:
                return
            kind, val = res
            if kind == "move":
                names = list(MENUS)
                self.open_menu(names[(names.index(title) + val) % len(names)])
            else:
                self.action_do(val)
        self.push_screen(MenuDrop(title, x), after)

    def action_menu(self, title: str) -> None:
        self.open_menu(title)

    # ---------------------------------------------------------------- actions
    MAIN_ONLY = {"why", "add_target", "select", "back", "refresh"}

    def action_do(self, what: str) -> None:
        if what in self.MAIN_ONLY and len(self.screen_stack) > 1:
            return            # single-key shortcuts only act on the main list
        getattr(self, f"do_{what}")()

    def do_quit(self) -> None:
        self.exit()

    @work(thread=True, exclusive=True, group="update")
    def do_update(self) -> None:
        self.call_from_thread(self.notify, "Downloading EiBi schedule and space weather...")
        try:
            season = eibi.download(self.settings.stations.season)
            self.sw = solar.update()
            self.sched = eibi.Schedule.load(self.settings)
        except Exception as e:
            self.call_from_thread(self.notify, f"Update failed: {e}", severity="error")
            return
        self.call_from_thread(self.notify, f"EiBi {season}: {len(self.sched.entries)} broadcasts "
                              f"({self.sched.aoki_status or 'AOKI: not available'}). "
                              f"{self.sched.mw_status}. {self.sw.summary}")
        self.call_from_thread(self.render_view)

    def do_open_folder(self) -> None:
        d = self.settings.recordings_dir
        d.mkdir(parents=True, exist_ok=True)
        osutil.open_path(d)

    def do_best(self) -> None:
        self.view_time = None
        self.show_stations("best")

    def do_countries(self) -> None:
        self.view_time = None
        self.show_countries()

    def do_targets(self) -> None:
        self.view_time = None
        self.show_stations("targets")

    def do_refresh(self) -> None:
        self.sw = solar.current(allow_network=False)
        self.view_time = None if self.view != "plan" else self.view_time
        self.render_view()
        self.run_scan()

    def do_back(self) -> None:
        self.action_stop_play()
        if self.view.startswith("country:"):
            itu = self.view.split(":", 1)[1]
            self.show_countries()
            t = self.query_one("#table", DataTable)
            try:
                t.move_cursor(row=t.get_row_index(itu))
            except Exception:
                pass

    def do_why(self) -> None:
        key = self.selected_key()
        e, p = self.rows.get(key or ""), self.preds.get(key or "")
        if not e or not p:
            self.notify("Highlight a station first.")
            return
        s = self.sched
        if p.band == "MW":
            self.push_screen(MessageBox("Why this score?", self._why_mw(key, e, p)))
            return
        pw = propagation.guess_power_kw(e, self.settings.propagation.tx_power_kw)
        text = (
            f"{e.station}  {e.khz:g} kHz\n"
            f"{s.country_name(e.itu)} - {s.language_name(e.lang)} - beamed to {e.target or '?'}\n"
            f"Transmitter: {e.tx_site or '?'} ({s.country_name(e.tx_itu)})\n\n"
            f"Path: {p.distance_km:.0f} km, {p.hops} hop(s), bearing {p.bearing:.0f} deg from you\n\n"
            f"1) Will the ionosphere reflect it?\n"
            f"   Max usable frequency on this path now: {p.muf_mhz:.1f} MHz\n"
            f"   Station frequency: {e.khz / 1000:.2f} MHz  ->  P(MUF) = {100 * p.p_muf:.0f}%\n\n"
            f"2) Will it be loud enough?\n"
            f"   Assumed power {pw:g} kW, antenna gain towards you {p.tx_gain:+.0f} dBi\n"
            f"   Daytime D-layer absorption: {p.absorption_db:.0f} dB\n"
            f"   Predicted SNR: {p.snr_db:.0f} dB (need 15)  ->  P(SNR) = {100 * p.p_snr:.0f}%\n\n"
            f"Score = {100 * p.p_muf:.0f}% x {100 * p.p_snr:.0f}% = {p.score}%\n\n"
            f"Measured now: {self._measured_text(key)}\n"
            f"Learned correction for this transmitter: {calibration.offset(e):+.0f} dB "
            f"(included in the SNR above)\n"
            f"(space weather: {self.sw.summary})")
        self.push_screen(MessageBox("Why this score?", text))

    def _why_mw(self, key, e, p) -> str:
        s = self.sched
        src = ("MWLIST" if e.source == "MWLIST" else "AOKI list" if e.power_kw
               else "guessed - no schedule lists it")
        return (
            f"{e.station}  {e.khz:g} kHz  (medium wave)\n"
            f"{s.country_name(e.itu)} - {s.language_name(e.lang) or '?'}\n"
            f"Transmitter: {e.tx_site or '?'}, {p.power_kw:g} kW ({src})\n\n"
            f"Path: {p.distance_km:.0f} km, bearing {p.bearing:.0f} deg from you\n\n"
            f"Medium wave travels two ways:\n"
            f"   Ground wave (day and night):   {p.e_ground:5.0f} dB(uV/m)\n"
            f"   Sky wave via the E layer:      {p.e_sky:5.0f} dB(uV/m)\n"
            f"   (daylight loss on the sky wave: {p.absorption_db:.0f} dB - it only\n"
            f"    really opens once the path is dark)\n"
            f"   -> {p.note}\n\n"
            f"Predicted SNR: {p.snr_db:.0f} dB (need 15)  ->  Score {p.score}%\n"
            f"(no MUF on medium wave: the ionosphere always reflects it at night)\n\n"
            f"Measured now: {self._measured_text(key)}\n"
            f"Learned correction for this transmitter: {calibration.offset(e):+.0f} dB "
            f"(included above)\n"
            f"(space weather: {self.sw.summary})")

    def _measured_text(self, key) -> str:
        m = self.meas.get(key)
        if m is None:
            return "not scanned (Ctrl+R scans the bands)"
        if m == float("-inf"):
            return "nothing on this frequency in the last scan"
        return f"carrier {m:.0f} dB above the noise (5 kHz channel)"

    def do_add_target(self) -> None:
        e = self.selected_entry()
        if not e:
            return
        name = self.sched.country_name(e.itu).split(" (")[0]
        targets = self.settings.mission.targets
        if name not in targets:
            targets.append(name)
            self.settings.save()
        self.notify(f"Night targets: {', '.join(targets)}")

    def do_select(self) -> None:
        if not self.sched:
            return

        def after(targets):
            if targets is not None:
                self.settings.mission.targets = targets
                self.settings.save()
                self.show_stations("targets")
        self.push_screen(SelectTargets(self.sched, self.settings.mission.targets), after)

    def do_at_time(self) -> None:
        def after(v):
            if v:
                try:
                    h, m = map(int, v.split(":"))
                    now = datetime.now(timezone.utc)
                    self.view_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
                except ValueError:
                    self.notify("Use HH:MM", severity="error")
                    return
                self.render_view()
        self.push_screen(Prompt("Show at UTC time", "UTC time (HH:MM), today:", "02:00"), after)

    def _mission_busy(self) -> bool:
        if self.scanning:
            self.notify("Band scan in progress - try again in a few seconds.")
            return True
        if mission.read_state().get("state") == "recording":
            self.notify("A night mission is using the radio right now.", severity="error")
            return True
        return False

    def do_record(self) -> None:
        e = self.selected_entry()
        if not e:
            self.notify("Highlight a station first (F2).")
            return
        if not self._mission_busy():
            self.push_screen(RecordDialog(e))

    def do_record_freq(self) -> None:
        def after(v):
            if not v:
                return
            try:
                khz = float(v)
            except ValueError:
                return
            now = datetime.now(timezone.utc)
            hits = [x for x in (self.sched.on_air(now) if self.sched else []) if abs(x.khz - khz) < 0.6]
            e = hits[0] if hits else eibi.Entry(khz, 0, 0, "", "XUU", f"Unknown {khz:g} kHz",
                                                "", "", "", "", "", "")
            if not self._mission_busy():
                self.push_screen(RecordDialog(e))
        self.push_screen(Prompt("Record a frequency", "Frequency in kHz:", "9420"), after)

    def play(self, path: str) -> None:
        self.player.play(path)
        self.last_file = path
        self.notify(f"Playing {Path(path).name}  (Record > Stop playback)")

    def action_stop_play(self) -> None:
        self.player.stop()

    def do_play_last(self) -> None:
        f = self.last_file
        if not f:
            rows = logbook.rows(logbook.connect(), "file IS NOT NULL", limit=1)
            f = rows[0]["file"] if rows else None
        if f:
            self.play(f)

    def do_stop_play(self) -> None:
        self.action_stop_play()

    def do_night(self) -> None:
        self.push_screen(NightDialog(), lambda r: self.show_plan() if r == "plan" else None)

    def do_plan(self) -> None:
        self.show_plan()

    def do_stop_mission(self) -> None:
        self.notify("Mission stopped." if mission.stop_background() else "No mission running.")

    def do_log(self) -> None:
        self.push_screen(LogDialog(), lambda r: self.do_report() if r == "report" else None)

    @work(thread=True, exclusive=True, group="classify")
    def do_classify(self) -> None:
        from . import core
        rows = logbook.rows(logbook.connect(), "file IS NOT NULL AND label IS NULL", limit=10000)
        self.call_from_thread(self.notify, f"Classifying {len(rows)} recordings...")
        for r in rows:
            core.classify_row(r["id"], self.settings, log=lambda m: None)
        self.call_from_thread(self.notify, "Classification done.")

    def do_report(self) -> None:
        ms = logbook.missions(logbook.connect())
        name = ms[0] if ms else None
        path = report.write(mission=name, settings=self.settings)
        text = report.text_summary(report._select(name, None))
        self.last_report = path
        self.push_screen(MessageBox(f"Report {name or 'latest recordings'}",
                                    f"{text}\n\nHTML: {path}\n(Log > Open HTML report in browser)"))

    def do_open_report(self) -> None:
        path = getattr(self, "last_report", None)
        if not path:
            ms = logbook.missions(logbook.connect())
            path = report.write(mission=ms[0] if ms else None, settings=self.settings)
        from . import server
        # the day-by-day log through the local server (Send report, QSL cards, navigation)
        osutil.open_url(f"{server.base_url(self.settings)}/log/")

    def do_cards(self) -> None:
        from . import server
        osutil.open_url(f"{server.base_url(self.settings)}/cards")

    def do_setup(self) -> None:
        def after(r):
            if r == "saved":
                self.settings = config.load()
                self.notify("Setup saved.")
                if self.sched:
                    self.sched = eibi.Schedule.load(self.settings)
                    self.render_view()
        self.push_screen(SetupWizard(), after)

    def do_settings(self) -> None:
        def after(r):
            if r == "saved":
                self.settings = config.load()
                self.notify("Settings saved.")
                if self.sched:
                    self.sched = eibi.Schedule.load(self.settings)
                    self.render_view()
        self.push_screen(SettingsDialog(), after)

    def do_edit_config(self) -> None:
        osutil.edit_text(config.CONFIG_PATH)

    def do_help(self) -> None:
        self.push_screen(MessageBox("Help", HELP.replace("{home}", str(config.HOME))
                                    + f"\n\nData folder: {config.HOME}\n"
                                    f"Recordings:  {self.settings.recordings_dir}"))

    def do_about(self) -> None:
        from . import __version__
        self.push_screen(MessageBox("About", f"NightDX {__version__}\n\nSchedules: EiBi (eibispace.de) + AOKI list\n"
                                    "Space weather: NOAA SWPC\nClassifier: AST fine-tuned on "
                                    "AudioSet (MIT)\nReceiver: AirSpy HF+ via libairspyhf"))


def run(setup: bool = False) -> None:
    app = NightDX()
    app.force_setup = setup
    app.run()
