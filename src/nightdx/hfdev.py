"""Direct access to the AirSpy HF+ through libairspyhf (ctypes).

airspyhf_rx re-opens the device for every run (~2.5 s). For band scans we
open it once, keep it streaming, and just retune between captures.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import threading

import numpy as np

from .osutil import airspyhf_library_candidates


class _Transfer(ctypes.Structure):
    _fields_ = [("device", ctypes.c_void_p), ("ctx", ctypes.c_void_p),
                ("samples", ctypes.POINTER(ctypes.c_float)), ("sample_count", ctypes.c_int),
                ("dropped_samples", ctypes.c_uint64)]


_CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(_Transfer))


def _lib():
    for p in airspyhf_library_candidates():
        if p and (os.path.exists(p) or os.sep not in p):
            try:
                lib = ctypes.CDLL(p)
            except OSError:          # e.g. an Intel build on Apple Silicon
                continue
            lib.airspyhf_open.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
            lib.airspyhf_open_sn.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint64]
            for name in ("close", "stop", "is_streaming"):
                getattr(lib, f"airspyhf_{name}").argtypes = [ctypes.c_void_p]
            lib.airspyhf_start.argtypes = [ctypes.c_void_p, _CB, ctypes.c_void_p]
            lib.airspyhf_set_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            lib.airspyhf_set_samplerate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            for name in ("set_hf_agc", "set_hf_agc_threshold", "set_hf_lna"):
                getattr(lib, f"airspyhf_{name}").argtypes = [ctypes.c_void_p, ctypes.c_uint8]
            return lib
    from .osutil import driver_hint
    raise RuntimeError("no usable AirSpy HF+ library. " + driver_hint())


class HFPlus:
    """with HFPlus(768000, settings.receiver) as hf: iq = hf.capture(9.6e6, 0.5)"""

    def __init__(self, sample_rate: int, rx=None):
        self.sr = sample_rate
        self.rx = rx
        self.lib = _lib()
        self.dev = ctypes.c_void_p()
        self._lock = threading.Lock()
        self._want = 0
        self._skip = 0
        self._chunks: list[np.ndarray] = []
        self._done = threading.Event()
        self._sink = None
        self._cb = _CB(self._on_samples)   # keep a reference: the C side calls it

    def __enter__(self):
        sn = (self.rx.serial if self.rx else "") or ""
        rc = (self.lib.airspyhf_open_sn(ctypes.byref(self.dev), int(sn, 16)) if sn
              else self.lib.airspyhf_open(ctypes.byref(self.dev)))
        if rc != 0 or not self.dev:
            raise RuntimeError("cannot open the AirSpy HF+ (not plugged in, or another program is using it)")
        self.lib.airspyhf_set_samplerate(self.dev, self.sr)
        if self.rx is not None:
            self.lib.airspyhf_set_hf_agc(self.dev, 1 if self.rx.agc else 0)
            self.lib.airspyhf_set_hf_agc_threshold(self.dev, 1 if self.rx.agc_threshold == "high" else 0)
            self.lib.airspyhf_set_hf_lna(self.dev, 1 if self.rx.lna else 0)
        if self.lib.airspyhf_start(self.dev, self._cb, None) != 0:
            self.lib.airspyhf_close(self.dev)
            raise RuntimeError("cannot start streaming from the AirSpy HF+")
        return self

    def __exit__(self, *exc):
        self.lib.airspyhf_stop(self.dev)
        self.lib.airspyhf_close(self.dev)

    def _on_samples(self, t) -> int:
        tr = t.contents
        n = tr.sample_count
        with self._lock:
            if self._want <= 0:
                return 0
            if self._skip > 0:
                self._skip -= n
                return 0
            buf = np.ctypeslib.as_array(tr.samples, shape=(n * 2,)).copy().view(np.complex64)
            if self._sink is not None:
                self._sink.put(buf)
            else:
                self._chunks.append(buf)
            self._want -= n
            if self._want <= 0:
                self._done.set()
        return 0

    def stream(self, freq_hz: float, total: int, settle_s: float = 0.0):
        """Yield consecutive IQ blocks (complex64) until `total` samples were delivered."""
        import queue
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._chunks, self._want = [], 0
        self._sink = q
        self.lib.airspyhf_set_freq(self.dev, int(freq_hz))
        with self._lock:
            self._skip = int(settle_s * self.sr)
            self._want = total
        got = 0
        try:
            while got < total:
                block = q.get(timeout=5)
                got += len(block)
                yield block
        finally:
            with self._lock:
                self._want = 0
            self._sink = None

    def capture(self, freq_hz: float, seconds: float, settle_s: float = 0.05) -> np.ndarray:
        with self._lock:
            self._chunks = []
            self._done.clear()
            self._want = 0
        self.lib.airspyhf_set_freq(self.dev, int(freq_hz))
        with self._lock:
            self._skip = int(settle_s * self.sr)
            self._want = int(seconds * self.sr)
        if not self._done.wait(seconds + settle_s + 3):
            raise RuntimeError("timeout waiting for samples")
        return np.concatenate(self._chunks)
