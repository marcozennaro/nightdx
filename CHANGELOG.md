# Changelog

## 0.9.1 - 2026-10-19

First fixes from testing on Windows with a real AirSpy HF+.

* **Windows driver:** NightDX loads `airspyhf.dll` together with its helper DLLs
  (`libusb-1.0.dll`, `pthreadVC2.dll`) from `%LOCALAPPDATA%\NightDX\`. The README and the setup
  wizard now explain where to get them (the SDR# x64 package from airspy.com).
* **Setup wizard and Settings fit small windows:** the *Save* / *Cancel* buttons are always
  visible, the questions scroll above them, and dialogs are never wider than the screen.
* **"Starting NightDX..."** is printed immediately, so a slow first start doesn't look like a hang.
* **Cleaner AI tags:** only radio-relevant labels (speech, music, genres, instruments, static) are
  shown and stored - no more "Vehicle", "Insect" or worse for noisy shortwave audio.
* The "why this score?" box names the real source of a medium-wave transmitter's power (AOKI).
* The night-mission dialog says "the computer", not "the Mac", is kept awake.
* README: step-by-step installation for macOS, Windows and Linux, updating, troubleshooting,
  screenshots.

## 0.9.0 - 2026-10-19

First release candidate: EiBi + AOKI schedules, HF/MW propagation with self-calibration, band
scans, night missions with discovery mode, optional AI classification and transcription,
reception reports as Gmail drafts, day-by-day browser log, QSL card collection, setup wizard,
macOS / Windows / Linux support.
