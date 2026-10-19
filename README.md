# NightDX

**Unattended shortwave and medium-wave listening with the AirSpy HF+**, in a menu-driven
terminal interface that looks like 1992.

Arm a *night mission* before going to bed. NightDX scans the bands, records the stations that
are actually coming in (preferring the ones you have never heard), works out whether each
recording holds speech, music or just noise, and in the morning gives you a report with audio
players, ready-made reception reports for the stations, and a log of the QSL cards you receive.

* **Schedules**: [EiBi](http://www.eibispace.de) + the [AOKI list](http://www1.s2.starcat.ne.jp/ndxc/)
  (downloaded automatically), optionally your [MWLIST](https://www.mwlist.org) medium-wave list.
* **Propagation**: a compact VOACAP-like HF model and an ITU-R ground/sky-wave model for MW,
  with live solar flux and Kp from NOAA - plus **self-calibration** from your own band scans.
* **Band scan**: the HF+ snapshots every band in ~10 s and measures what is really audible.
* **Night missions** with a *discovery* mode that goes after stations you haven't logged yet.
* **AI** (optional): speech / music / noise classification (AudioSet transformer) and programme
  transcription + translation (Whisper) for your reception reports.
* **Reception reports**: one click prepares an e-mail (SINPO, programme details, your QSL request)
  - as a Gmail draft with the recording attached, or in any mail program.
* **Day-by-day log** in the browser, **QSL card collection** with "already received" tracking.

## Screenshots

**Main screen** - countries ranked by what the band scan actually hears ("Heard dB") next to the
propagation prediction, with the radio, schedules and space weather at the top:

![Main screen](docs/images/main-screen.webp)

**Night mission** - choose the hours and (optionally) targets, then arm it for tonight:

![Night mission dialog](docs/images/night-mission.png)

**Morning report** - every recording with the AI verdict, an audio player and a reception-report
button that remembers which stations you have already written to:

![Morning report](docs/images/morning-report.webp)

## Requirements

* An **AirSpy HF+** (Discovery or Dual Port) and an antenna.
* macOS 12+, Windows 10/11, or Linux. Python is installed for you by `uv` (below).
* Internet access for schedules and space weather.

> **Status:** developed and used daily on macOS (Apple Silicon). Windows and Linux are supported
> and covered by the automatic tests, but have not yet been tested with a real HF+ - reports welcome!

## Install

### 1. Install `uv` (the Python installer)

* **macOS / Linux**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
* **Windows** (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 2. Install the AirSpy HF+ driver

* **macOS**: `brew install airspyhf` (install [Homebrew](https://brew.sh) first if needed).
* **Windows**: download `airspyhf.dll` from the
  [airspyhf releases](https://github.com/airspy/airspyhf/releases) (or copy it from your SDR#
  folder) into `%LOCALAPPDATA%\NightDX\`. Windows 10/11 normally installs the HF+'s USB driver
  automatically; if the radio isn't found, install the WinUSB driver with [Zadig](https://zadig.akeo.ie).
* **Linux**: `sudo apt install libairspyhf-dev airspyhf` (or build
  [airspyhf](https://github.com/airspy/airspyhf)), plus `libportaudio2` for audio playback.

Close other SDR programs while NightDX uses the radio - only one program can use it at a time.

### 3. Install NightDX

```bash
uv tool install "nightdx @ git+https://github.com/marcozennaro/nightdx"
```

then start it with `nightdx`. To include the optional features from the start (they can also be
installed later from the setup wizard):

```bash
uv tool install "nightdx[all] @ git+https://github.com/marcozennaro/nightdx"
```

| Extra | What it adds | Size |
|---|---|---|
| `ai` | speech/music/noise classification, programme transcription | ~2 GB + models on first use |
| `gmail` | Gmail drafts with the recording attached | ~15 MB |
| `images` | iPhone (HEIC) photos of QSL cards on Windows/Linux | ~20 MB |

## First start

`nightdx` opens the **setup wizard**: your location (name, country, coordinates), shortwave only
or shortwave + medium wave, a check for the radio, the optional features (with an *Install*
button), where to keep recordings, and your details for reception reports. Run it again any time
from *Settings > Setup wizard* or with `nightdx setup`.

Then press **F1** for the keys. The essentials:

| Key | | Key | |
|---|---|---|---|
| F2 | stations on the air now | F6 | night mission (arm tonight / start now) |
| F3 | most likely countries | F7 | listening log |
| F4 | preview tonight's plan | F8 | report |
| F5 / Enter | record the highlighted station | F9 | settings |
| I | why this score? | Ctrl+R | scan the bands now |

The day-by-day log, reception-report buttons and QSL cards live in your browser:
*Log > Open HTML report in browser*.

## Reception reports and Gmail

Each heard recording has a **Send report** button. Without any setup it opens a pre-filled
e-mail in Gmail or your mail program (attach the recording yourself). With the `gmail` extra
and a one-time setup, NightDX creates a real **Gmail draft with the recording attached**:

1. In the [Google Cloud console](https://console.cloud.google.com/) create a project and enable
   the **Gmail API**.
2. *OAuth consent screen*: type External; add your own address under *Test users*.
3. *Credentials > Create credentials > OAuth client ID*: type **Desktop app**; download the JSON
   and save it as `gmail_credentials.json` in your NightDX data folder (see below).
4. Run `nightdx gmail-setup` and approve. NightDX only asks to *create drafts*; it never reads
   your mail and never sends anything - you press Send.

Station addresses come from `qsl_contacts.json` (researched from station pages and DX-club QSL
logs, with sources). Add your own in `qsl_contacts.json` in the data folder; yours take precedence.

## Medium wave and MWLIST

EiBi and AOKI cover international MW broadcasters. For the full picture, register (free) at
[fmlist.org](https://www.fmlist.org), log in at [fmscan.org](https://www.fmscan.org), set your
location, and download *Tools > Userlist for Perseus / ELAD / Winradio / SDR Console*
(LW/MW/SW, CSV, separator Tab). Put the file in the `mwlist` folder inside the data folder and
press Ctrl+U. MWLIST data is for personal, non-commercial use.

## Where your data lives

Nothing is stored in the program folder:

| | macOS | Windows | Linux |
|---|---|---|---|
| settings, log, schedules, Gmail token, QSL cards | `~/Library/Application Support/NightDX` | `%APPDATA%\NightDX` | `~/.local/share/NightDX` |
| recordings (changeable in the wizard) | `~/Documents/NightDX/recordings` | `Documents\NightDX\recordings` | `~/Documents/NightDX/recordings` |

Set `NIGHTDX_HOME` to use another data folder.

## Command line

Everything also works without the interface (handy for cron / Task Scheduler):

```bash
nightdx update                 # schedules + space weather
nightdx best -n 20             # what's on the air now, best first
nightdx record 9420            # record a frequency now
nightdx plan                   # preview tonight's mission
nightdx night --now --end 06:30
nightdx report
```

## How it works (short version)

* **Propagation** - great-circle path in F2 hops; foF2 follows local time, solar flux and Kp;
  MUF from hop geometry; George & Bradley D-layer absorption; ITU-R P.372 noise. MW uses an
  ITU-R P.368 ground-wave fit and the ITU-R P.1147 sky-wave formula. After every band scan the
  difference between prediction and measurement is learned per station and beam.
* **Band scan** - 768 kHz snapshots (~0.5 s each) straight from libairspyhf; each scheduled
  carrier is compared with the local noise floor.
* **Recording** - AM demodulation from the IQ stream, saved as FLAC with full station metadata.
* **AI** - the [AST AudioSet model](https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593)
  labels 10 s windows; [Whisper](https://huggingface.co/openai/whisper-small) transcribes and
  translates speech for the reception report (weak signals are left blank rather than guessed).

## Credits

Schedules: EiBi (Eike Bierwirth), AOKI list / Bi Newsletter (Nagoya DX Circle). Space weather:
NOAA SWPC. MW data: MWLIST / FMSCAN (personal use). Models: MIT AST, OpenAI Whisper.
AirSpy HF+ driver: libairspyhf.

## License

MIT - see [LICENSE](LICENSE).
