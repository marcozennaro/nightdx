"""Tune the AirSpy HF+ with airspyhf_rx, demodulate AM, and measure the signal.

airspyhf_rx streams complex float32 IQ to stdout. We tune `tune_offset_hz`
above the station, shift it back to 0 Hz in software, low-pass, decimate
straight to the audio rate, and envelope-detect. Along the way we average a
spectrum to measure the carrier and the sideband (modulation) level against
the local noise floor - useful evidence for the classifier.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass

import numpy as np
from scipy import signal

from .config import Settings


class ReceiverError(RuntimeError):
    pass


@dataclass
class Capture:
    audio: np.ndarray          # float32 mono, -1..1
    rate: int
    carrier_db: float          # carrier above noise floor, per ~12 Hz bin
    modulation_db: float       # sideband (200-4500 Hz) power above noise floor
    carrier_dbfs: float
    fading_db: float = 0.0     # std-dev of the carrier level over the recording (for SINPO "P")
    carriers: list | None = None   # [{offset_hz, rel_db, snr_db}], strongest first (identify.py)


def find_rx(settings: Settings) -> str:
    exe = shutil.which(settings.receiver.airspyhf_rx) or shutil.which("airspyhf_rx")
    if not exe:
        from .osutil import driver_hint
        raise ReceiverError("AirSpy HF+ tools not found. " + driver_hint())
    return exe


def device_info(settings: Settings) -> str:
    """'AirSpy HF+ <serial>' (which radio NightDX will use), via the driver library or the
    airspyhf_info tool."""
    try:
        from .hfdev import format_serial, list_serials, parse_serial
        serials = list_serials()
    except Exception:
        return _device_info_tool(settings)
    if not serials:
        return "not found - is the HF+ plugged in?"
    try:
        want = parse_serial(settings.receiver.serial)
    except ValueError as e:
        return f"{e} - fix it in Settings > Setup wizard"
    if want is None:
        return f"AirSpy HF+ {serials[0]}" + (
            f" (first of {len(serials)} - choose one in Settings > Setup wizard)" if len(serials) > 1 else "")
    if format_serial(want) in serials:
        return f"AirSpy HF+ {format_serial(want)}" + (
            f" (selected, {len(serials)} connected)" if len(serials) > 1 else "")
    return f"the HF+ {format_serial(want)} chosen in the settings is not connected - found {', '.join(serials)}"


def serial_args(rx) -> list[str]:
    """'-s 0x...' for the airspyhf_rx tool (which reads bare digits as decimal)."""
    from .hfdev import format_serial, parse_serial
    try:
        sn = parse_serial(rx.serial)
    except ValueError as e:
        raise ReceiverError(str(e))
    return ["-s", format_serial(sn)] if sn is not None else []


def _device_info_tool(settings: Settings) -> str:
    import os
    rx = find_rx(settings)
    sibling = os.path.join(os.path.dirname(rx), "airspyhf_info")
    exe = sibling if os.path.exists(sibling) else shutil.which("airspyhf_info")
    if not exe:
        return "airspyhf_info not found"
    try:
        out = subprocess.run([exe], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return f"unavailable ({e})"
    sn = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("S/N")), None)
    fw = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("Firmware")), "")
    return f"AirSpy HF+ {sn} fw {fw}" if sn else "not found (is another SDR app using it?)"


def record(khz: float, seconds: float, settings: Settings,
           progress=None, stop: threading.Event | None = None,
           finish: threading.Event | None = None) -> Capture:
    """`stop` aborts and discards; `finish` ends early and keeps what we have."""
    rx = settings.receiver
    sr = rx.sample_rate
    rate = settings.recording.audio_rate
    if sr % rate:
        raise ReceiverError(f"sample rate {sr} is not a multiple of audio rate {rate}")
    dec = sr // rate
    settle = 0.5                                    # drop AGC settling at the start
    total = int((seconds + settle) * sr)
    tune_hz = khz * 1000 + rx.tune_offset_hz

    # filters (stateful, so chunks join seamlessly)
    chan = signal.cheby2(10, 60, 5500, fs=sr, output="sos")
    dc = signal.butter(2, 60, "highpass", fs=rate, output="sos")
    aud = signal.butter(6, settings.recording.audio_bandwidth_hz, fs=rate, output="sos")
    zc = signal.sosfilt_zi(chan) * 0
    zd = signal.sosfilt_zi(dc) * 0
    za = signal.sosfilt_zi(aud) * 0

    nfft = 16384
    spec = np.zeros(nfft)
    nspec = 0
    phase = 0.0
    w = 2 * np.pi * rx.tune_offset_hz / sr
    chunk = (sr // dec) * dec // 4                  # 0.25 s, multiple of `dec`
    out = []
    base = []                                       # complex baseband at the audio rate
    levels: list[float] = []
    got = 0
    early = False

    source = _Source(settings, tune_hz, total)
    carry = np.zeros(0, dtype=np.complex64)
    try:
        for block in source.blocks():
            if stop is not None and stop.is_set():
                raise ReceiverError("recording cancelled")
            if finish is not None and finish.is_set() and got > (settle + 2) * sr:
                early = True
                break
            carry = np.concatenate([carry, block])
            if len(carry) < chunk and got + len(carry) < total:
                continue
            n = len(carry) - len(carry) % dec
            x, carry = carry[:n], carry[n:]
            mix = np.exp(1j * (phase + w * np.arange(n)))
            phase = (phase + w * n) % (2 * np.pi)
            y = x * mix.astype(np.complex64)
            got += n
            if got > settle * sr and len(y) >= nfft:
                spec += np.abs(np.fft.fftshift(np.fft.fft(y[:nfft] * np.hanning(nfft)))) ** 2
                nspec += 1
            y, zc = signal.sosfilt(chan, y, zi=zc)
            yd = y[::dec]
            env = np.abs(yd)
            if got > settle * sr:
                levels.append(20 * np.log10(env.mean() + 1e-12))
                base.append(yd.astype(np.complex64))
            a, zd = signal.sosfilt(dc, env, zi=zd)
            a, za = signal.sosfilt(aud, a, zi=za)
            out.append(a.astype(np.float32))
            if progress:
                progress(min(1.0, got / total))
    finally:
        err = source.close()
    if not early and got < total * 0.9:
        raise ReceiverError(f"the HF+ delivered {got / sr:.1f}s of {total / sr:.1f}s "
                            f"(is another SDR program using it?)\n{err[-400:]}")

    audio = np.concatenate(out)[int(settle * rate):]
    peak = np.percentile(np.abs(audio), 99.9) or 1.0
    audio = np.clip(audio / peak * 0.8, -1, 1).astype(np.float32)

    # signal measurements from the averaged spectrum
    spec /= max(nspec, 1)
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / sr))
    af = np.abs(freqs)
    noise = np.median(spec[(af > 6000) & (af < 60000)]) or 1e-20
    carrier = spec[af < 40].max()
    sideband = spec[(af > 200) & (af < 4500)].mean()
    try:
        from .identify import find_carriers
        cs = find_carriers(np.concatenate(base), rate) if base else []
        fix = getattr(rx, "freq_correction_ppm", 0.0) * khz / 1000   # ppm x MHz = Hz
        for c in cs:
            c["offset_hz"] = round(c["offset_hz"] + fix, 2)
    except Exception:
        cs = []
    return Capture(
        audio=audio, rate=rate, carriers=cs,
        carrier_db=float(10 * np.log10(carrier / noise)),
        modulation_db=float(10 * np.log10(sideband / noise)),
        carrier_dbfs=float(10 * np.log10(carrier / np.hanning(nfft).sum() ** 2 + 1e-20)),
        fading_db=float(np.std(levels)) if len(levels) > 4 else 0.0,
    )


class _Source:
    """IQ samples from the AirSpy HF+: through libairspyhf directly (every OS), or by
    running the airspyhf_rx tool (setting receiver.backend, default 'auto')."""

    def __init__(self, settings: Settings, tune_hz: float, total: int):
        self.settings, self.tune_hz, self.total = settings, tune_hz, total
        self.proc = None
        self.hf = None
        backend = getattr(settings.receiver, "backend", "auto")
        if backend in ("auto", "library"):
            from .hfdev import SerialNotFound
            try:
                from .hfdev import HFPlus
                self.hf = HFPlus(settings.receiver.sample_rate, settings.receiver).__enter__()
            except SerialNotFound as e:              # never fall back to "any radio"
                raise ReceiverError(str(e))
            except Exception as e:
                if backend == "library":
                    raise ReceiverError(str(e))
                self.hf = None
        if self.hf is None:
            self._start_tool()

    def _start_tool(self) -> None:
        rx = self.settings.receiver
        sr = rx.sample_rate
        cmd = [find_rx(self.settings), "-f", f"{self.tune_hz / 1e6:.6f}", "-a", str(sr),
               "-n", str(self.total), "-r", "stdout",
               "-g", "on" if rx.agc else "off", "-m", "on" if rx.lna else "off"]
        cmd += ["-l", rx.agc_threshold] if rx.agc else ["-t", str(rx.attenuation)]
        cmd += serial_args(rx)
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def blocks(self):
        if self.hf is not None:
            yield from self.hf.stream(self.tune_hz, self.total)
            return
        size = self.settings.receiver.sample_rate // 4 * 8
        while True:
            buf = self.proc.stdout.read(size)
            if not buf:
                return
            yield np.frombuffer(buf[: len(buf) - len(buf) % 8], dtype=np.complex64)

    def close(self) -> str:
        if self.hf is not None:
            try:
                self.hf.__exit__(None, None, None)
            except Exception:
                pass
            return ""
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        return self.proc.stderr.read().decode(errors="replace")


# ----------------------------------------------------------------------------- live listening

class LiveDemod:
    """The recording chain (offset mix, channel filter, envelope, audio filter) for live
    audio, chunk by chunk, plus the signal level of each chunk and a slow AGC so weak and
    strong stations play at a similar volume."""

    def __init__(self, settings: Settings):
        rx = settings.receiver
        self.sr, self.rate = rx.sample_rate, settings.recording.audio_rate
        if self.sr % self.rate:
            raise ReceiverError(f"sample rate {self.sr} is not a multiple of audio rate {self.rate}")
        self.dec = self.sr // self.rate
        self.w = 2 * np.pi * rx.tune_offset_hz / self.sr
        self.phase = 0.0
        self.chan = signal.cheby2(10, 60, 5500, fs=self.sr, output="sos")
        self.dc = signal.butter(2, 60, "highpass", fs=self.rate, output="sos")
        self.aud = signal.butter(6, settings.recording.audio_bandwidth_hz, fs=self.rate, output="sos")
        self.zc, self.zd, self.za = (signal.sosfilt_zi(f) * 0 for f in (self.chan, self.dc, self.aud))
        self.carry = np.zeros(0, dtype=np.complex64)
        self.gain = None
        self.nfft = 8192
        self.win = np.hanning(self.nfft)
        self.freqs = np.abs(np.fft.fftshift(np.fft.fftfreq(self.nfft, 1 / self.sr)))

    def process(self, block: np.ndarray) -> tuple[np.ndarray, float | None, float | None]:
        """IQ block -> (audio float32, carrier dB above noise, modulation dB above noise).
        The levels are None when the block is too short to measure."""
        x = np.concatenate([self.carry, block])
        n = len(x) - len(x) % self.dec
        x, self.carry = x[:n], x[n:]
        y = x * np.exp(1j * (self.phase + self.w * np.arange(n))).astype(np.complex64)
        self.phase = (self.phase + self.w * n) % (2 * np.pi)
        carrier = mod = None
        if n >= self.nfft:
            spec = np.abs(np.fft.fftshift(np.fft.fft(y[:self.nfft] * self.win))) ** 2
            noise = np.median(spec[(self.freqs > 6000) & (self.freqs < 60000)]) or 1e-20
            carrier = float(10 * np.log10(spec[self.freqs < 40].max() / noise))
            mod = float(10 * np.log10(spec[(self.freqs > 200) & (self.freqs < 4500)].mean() / noise))
        y, self.zc = signal.sosfilt(self.chan, y, zi=self.zc)
        env = np.abs(y[::self.dec])
        a, self.zd = signal.sosfilt(self.dc, env, zi=self.zd)
        a, self.za = signal.sosfilt(self.aud, a, zi=self.za)
        if len(a):
            peak = float(np.percentile(np.abs(a), 99)) or 1e-9
            g = 0.5 / peak
            self.gain = g if self.gain is None else min(g, self.gain * 0.8 + g * 0.2)
            a = np.clip(a * self.gain, -1, 1)
        return a.astype(np.float32), carrier, mod


def listen(settings: Settings, khz: float, stop: threading.Event, retune: threading.Event,
           on_level=None) -> None:
    """Play `khz` live through the computer's speakers until `stop` is set. When `retune`
    is set, return so the caller can start again on a new frequency."""
    import sounddevice as sd
    rx = settings.receiver
    demod = LiveDemod(settings)
    source = _Source(settings, khz * 1000 + rx.tune_offset_hz, rx.sample_rate * 3600 * 12)
    try:
        with sd.OutputStream(samplerate=demod.rate, channels=1, dtype="float32") as out:
            pending, have = [], 0
            for block in source.blocks():
                if stop.is_set() or retune.is_set():
                    break
                pending.append(block)               # USB blocks are small: work in 0.1 s chunks
                have += len(block)
                if have < rx.sample_rate // 10:
                    continue
                chunk, pending, have = np.concatenate(pending), [], 0
                audio, carrier, mod = demod.process(chunk)
                if len(audio):
                    out.write(audio.reshape(-1, 1))
                if on_level and carrier is not None:
                    on_level(carrier, mod)
    finally:
        source.close()
