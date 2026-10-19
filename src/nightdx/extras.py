"""Optional features, installable from the setup wizard.

They are installed into the Python environment NightDX itself runs in, with
uv if available (uv tool / uv venv environments have no pip) or else pip.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys

FEATURES = {
    "ai": {
        "title": "AI: speech/music/noise classification + programme transcription",
        "size": "about 2 GB (PyTorch); the models (~1.3 GB) download on first use",
        "packages": ["torch", "transformers"],
        "modules": ["torch", "transformers"],
    },
    "gmail": {
        "title": "Gmail drafts with the recording attached",
        "size": "about 15 MB; needs a one-time Google Cloud setup (see README)",
        "packages": ["google-api-python-client", "google-auth-oauthlib"],
        "modules": ["googleapiclient", "google_auth_oauthlib"],
    },
    "images": {
        "title": "iPhone (HEIC) photos of QSL cards on Windows/Linux",
        "size": "about 20 MB (not needed on macOS)",
        "packages": ["pillow", "pillow-heif"],
        "modules": ["PIL", "pillow_heif"],
    },
}


def installed(feature: str) -> bool:
    return all(importlib.util.find_spec(m) is not None for m in FEATURES[feature]["modules"])


def install_command(features: list[str]) -> list[str]:
    pkgs = [p for f in features for p in FEATURES[f]["packages"]]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *pkgs]
    return [sys.executable, "-m", "pip", "install", *pkgs]


def install(features: list[str], log=print) -> bool:
    """Install the packages, streaming the installer's output to `log`."""
    cmd = install_command(features)
    log("$ " + " ".join(cmd))
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as e:
        log(f"could not start the installer: {e}")
        return False
    for line in p.stdout:
        log(line.rstrip())
    ok = p.wait() == 0
    importlib.invalidate_caches()
    log("done." if ok else f"installer failed (exit code {p.returncode})")
    return ok
