# Changelog

## 0.9.8 - 2026-10-26

* **Better self-calibration on shortwave.** The corrections NightDX learns from your band scans
  are now split into a receiver-wide part (your antenna, your local noise, the model's bias -
  about -33 dB in Trieste), applied to every station, and each station's own deviation, kept
  within +-10 dB. Before, stations never scanned got no correction at all and so jumped ahead of
  the calibrated ones, and a few wild values (up to +-40 dB) went straight into the predictions.
  On ~800 shortwave recordings: AUC 0.68 -> 0.72, rank correlation 0.38 -> 0.48. Medium wave,
  where the per-station corrections work well, is unchanged. Existing learned data is reused.
* Every scan measurement used for learning is also kept in `scan_pairs.jsonl` (data folder,
  max 20 MB), so future changes to the calibration can be tested on real history.
* *Why this score?* shows both parts of the learned correction.

## 0.9.7 - 2026-10-26

* **HFCC data for better shortwave predictions.** NightDX now also downloads the HFCC public
  schedule - the broadcasters' own coordinated frequency list - and adds the real transmitter
  power, beam direction and antenna type to the EiBi entries it matches (about 3,000 of 4,800
  shortwave broadcasts). Tested on ~1,000 recordings from Trieste: the ranking of heard vs not
  heard stations improved from 0.64 to 0.68 (AUC) and the correlation of predicted with measured
  signal from 0.37 to 0.48.
* *Why this score?* (key I) shows the HFCC power, antenna gain and beam direction.

## 0.9.6 - 2026-10-26

* **Your own stations:** *Settings > My stations list* opens `my_stations.csv`, a simple list
  (kHz; UTC; days; station; country; language; ...) that joins EiBi/AOKI/MWLIST in predictions,
  scans, missions and reports. NightDX reloads it as soon as you save it and reports any line it
  can't read.
* **Choosing the radio with two or more HF+:** the setup wizard lists the connected radios by
  serial number. The main screen shows which one is in use.
* Fixed: a serial number written without quotes in `config.toml` was misread, and a radio that
  wasn't connected could silently fall back to another one. Serial numbers are now accepted as
  `0x3952C8F0A1B2C3D4`, `3952c8f0a1b2c3d4`, `S/N: 0x...`.
* `config.toml` edited by hand while NightDX runs is picked up at once, so a later *Save* in a
  dialog no longer overwrites your edit.

## 0.9.5 - 2026-10-25

* **Station names are now clearly candidates.** The report and the log show whether the
  evidence supports the scheduled station: *probably another station* (the language heard or the
  signal strength doesn't fit), *consistent with the schedule* (the language matches), or
  *candidate - not verified*. Thanks to the AirSpy group for the push.
* **Other stations on the channel:** each new recording lists the other stations scheduled on
  that frequency at that time, and points out the one whose language fits what was heard.
* **Carrier offsets:** every recording measures the carriers within +-25 Hz to about 0.01 Hz -
  a fingerprint of MW transmitters - and shows co-channel carriers. New setting
  `receiver.freq_correction_ppm` for your radio's frequency error.
* Reception reports for a flagged recording ask for confirmation first (browser and terminal).

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
