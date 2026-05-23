"""Payload location.

When the installer is built with PyInstaller, ScreenSense.exe and autorun.inf
are bundled as data files. PyInstaller extracts them to a temp dir at runtime
and exposes the path via sys._MEIPASS.

During development (running from source), the payload lives in a sibling
'payload/' directory.
"""
from __future__ import annotations

import sys
from pathlib import Path


def payload_dir() -> Path:
    """Returns directory containing ScreenSense.exe and autorun.inf."""
    if getattr(sys, "frozen", False):
        # PyInstaller-frozen: files are in the _MEIPASS temp dir
        return Path(sys._MEIPASS) / "payload"
    # Development: look in ../payload/ relative to this file
    return Path(__file__).resolve().parent.parent / "payload"


def screensense_exe() -> Path:
    return payload_dir() / "ScreenSense.exe"


def autorun_inf() -> Path:
    return payload_dir() / "autorun.inf"


def install_autorun_bat() -> Path:
    return payload_dir() / "Install-Autorun.bat"


def payload_files() -> list[Path]:
    """All files the installer copies to the USB drive."""
    return [p for p in [screensense_exe(), autorun_inf(), install_autorun_bat()] if p.exists()]


def payload_total_size() -> int:
    return sum(p.stat().st_size for p in payload_files())


def payload_available() -> bool:
    return screensense_exe().exists()
