"""Operating-system specifics (macOS, Windows, Linux) in one place."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from contextlib import contextmanager
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")


def open_url(url: str) -> None:
    webbrowser.open(url)


def open_path(path: Path | str) -> None:
    """Open a folder or file with the system's default application."""
    p = str(path)
    if IS_WIN:
        os.startfile(p)                                  # noqa: S606 (Windows only)
    elif IS_MAC:
        subprocess.Popen(["open", p])
    else:
        subprocess.Popen(["xdg-open", p])


def edit_text(path: Path | str) -> None:
    if IS_MAC:
        subprocess.Popen(["open", "-t", str(path)])
    elif IS_WIN:
        subprocess.Popen(["notepad.exe", str(path)])
    else:
        open_path(path)


# --------------------------------------------------------------------------- keep awake

@contextmanager
def keep_awake():
    """Stop the computer from sleeping while a night mission runs."""
    proc = None
    if IS_MAC and shutil.which("caffeinate"):
        proc = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
    elif IS_WIN:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    elif shutil.which("systemd-inhibit"):
        proc = subprocess.Popen(["systemd-inhibit", "--what=sleep:idle", "--why=NightDX mission",
                                 "sleep", "infinity"])
    try:
        yield
    finally:
        if proc:
            proc.terminate()
        if IS_WIN:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


# --------------------------------------------------------------------------- audio

class Player:
    """Plays a recording in the background; stop() works on every OS."""

    def __init__(self):
        self._proc = None

    def play(self, path: str) -> None:
        self.stop()
        try:
            import sounddevice as sd
            import soundfile as sf
            data, rate = sf.read(path, dtype="float32")
            sd.play(data, rate)
            return
        except Exception:
            pass                                          # no audio device / library
        if IS_MAC:
            self._proc = subprocess.Popen(["afplay", path])
        else:
            open_path(path)                               # fall back to the default player

    def stop(self) -> None:
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


# --------------------------------------------------------------------------- images

def to_jpeg(src: Path) -> Path | None:
    """HEIC/TIFF/BMP -> JPEG so browsers can show it. None if no converter is available."""
    dst = src.with_suffix(".jpg")
    if IS_MAC and shutil.which("sips"):
        r = subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
                           capture_output=True)
        return dst if r.returncode == 0 and dst.exists() else None
    try:
        from PIL import Image
        try:
            import pillow_heif                              # optional, for iPhone photos
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        Image.open(src).convert("RGB").save(dst, "JPEG", quality=90)
        return dst
    except Exception:
        return None


# --------------------------------------------------------------------------- AirSpy library

def airspyhf_library_candidates() -> list[str]:
    import ctypes.util
    if IS_WIN:
        dirs = [os.path.dirname(sys.executable), os.getcwd(),
                os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "airspyhf"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "NightDX")]
        out = [os.path.join(d, "airspyhf.dll") for d in dirs]
        found = ctypes.util.find_library("airspyhf")
        return out + ([found] if found else [])
    if IS_MAC:
        return ["/opt/homebrew/lib/libairspyhf.dylib", "/usr/local/lib/libairspyhf.dylib",
                ctypes.util.find_library("airspyhf") or ""]
    return ["libairspyhf.so.0", "libairspyhf.so", ctypes.util.find_library("airspyhf") or ""]


def driver_hint() -> str:
    if IS_MAC:
        return "Install the AirSpy HF+ driver with Homebrew:  brew install airspyhf"
    if IS_WIN:
        folder = os.path.join(os.environ.get("LOCALAPPDATA", ""), "NightDX")
        return ("Download SDR# x64 (sdrsharp-x64.zip) from airspy.com/download and copy "
                f"airspyhf.dll, libusb-1.0.dll and pthreadVC2.dll into {folder}")
    return "Install libairspyhf (e.g. sudo apt install libairspyhf-dev airspyhf) "


# --------------------------------------------------------------------------- processes

def pid_alive(pid) -> bool:
    """Is this process still running? (os.kill(pid, 0) would *terminate* it on Windows.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if IS_WIN:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def spawn_detached(cmd: list[str], log_path: Path) -> int:
    """Start a background process that survives closing the terminal."""
    with open(log_path, "a") as f:
        if IS_WIN:
            flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, creationflags=flags)
        else:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
    return p.pid
