# Changelog

## 0.9.4 - 2026-10-24

* **Gmail permission expired ("invalid_grant"):** NightDX no longer stops with an error. The
  next click on *Send report* opens Google's consent page again; after you approve, the draft is
  created as usual. Background status checks never open a consent page.
* README: publish the Google Cloud app (*Audience > Publish app*) so the permission doesn't
  expire every 7 days.

## 0.9.3 - 2026-10-24

* **Reception reports straight from the app:** a *QSL report* button in the recording window
  (as soon as the recording is saved) and in the listening log (F7, key **Q**) prepares the
  Gmail draft with the recording attached - no need to go through the browser report.
* NightDX asks first if you already reported to that station, have a draft waiting, or already
  received its QSL card.
* Recordings the AI labelled "noise" can be reported too (with a note) - you decide, since you
  listened.

## 0.9.2 - 2026-10-19

* **Recording window:** while a recording plays, *Play* is greyed out and *Stop* is active;
  *Play* becomes available again when playback ends or is stopped.
* **Windows:** arming a night mission no longer opens a second console window - the mission
  runs hidden in the background (and keeps running if you close NightDX). Stop it with
  *Night > Stop running mission*.

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
