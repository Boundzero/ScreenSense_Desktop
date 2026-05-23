"""Removable drive detection.

Windows-only. Uses ctypes to query drive types from the Windows API so we
only show USB sticks and SD cards — never the user's C: drive. Safety first:
an installer that can overwrite a system drive is a recipe for disaster.
"""
from __future__ import annotations

import ctypes
import string
import sys
from dataclasses import dataclass
from pathlib import Path


# Windows GetDriveType return codes
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2     # USB sticks, SD cards, floppies
DRIVE_FIXED = 3         # internal HDD/SSD — NEVER write here
DRIVE_REMOTE = 4        # network shares
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6


@dataclass
class DriveInfo:
    letter: str           # e.g. "E:"
    label: str            # volume label, or "" if none
    total_bytes: int
    free_bytes: int
    drive_type: int
    serial: int = 0       # volume serial number — used to detect drive swaps

    @property
    def display_name(self) -> str:
        label = self.label or "Removable Drive"
        gb_free = self.free_bytes / (1024**3)
        gb_total = self.total_bytes / (1024**3)
        return f"{self.letter}\\  {label}  ({gb_free:.1f} GB free of {gb_total:.1f} GB)"

    @property
    def path(self) -> Path:
        return Path(f"{self.letter}\\")


def _get_drive_type(letter: str) -> int:
    if sys.platform != "win32":
        return DRIVE_UNKNOWN
    return ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\")


def _get_volume_info(letter: str) -> tuple[str, int]:
    """Returns (label, serial_number). Empty/zero if drive not readable."""
    if sys.platform != "win32":
        return ("", 0)
    buf = ctypes.create_unicode_buffer(261)
    fs_buf = ctypes.create_unicode_buffer(261)
    serial = ctypes.c_ulong(0)
    max_comp = ctypes.c_ulong(0)
    flags = ctypes.c_ulong(0)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            f"{letter}:\\", buf, 261,
            ctypes.byref(serial), ctypes.byref(max_comp), ctypes.byref(flags),
            fs_buf, 261,
        )
        return (buf.value if ok else "", serial.value if ok else 0)
    except OSError:
        return ("", 0)


def _get_space(letter: str) -> tuple[int, int]:
    """Returns (free_bytes, total_bytes)."""
    if sys.platform != "win32":
        return (0, 0)
    free = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    try:
        ok = ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            f"{letter}:\\", ctypes.byref(free), ctypes.byref(total), None,
        )
        return (free.value, total.value) if ok else (0, 0)
    except OSError:
        return (0, 0)


def list_removable_drives() -> list[DriveInfo]:
    """All currently-mounted removable drives. Excludes fixed/network/CD drives."""
    drives = []
    if sys.platform != "win32":
        # For dev on non-Windows: return a fake drive for UI testing.
        return [DriveInfo("E", "TEST_USB", 16 * 1024**3, 12 * 1024**3, DRIVE_REMOVABLE, serial=0xDEADBEEF)]

    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not (bitmask & (1 << i)):
            continue
        dtype = _get_drive_type(letter)
        if dtype != DRIVE_REMOVABLE:
            continue
        free, total = _get_space(letter)
        if total == 0:
            continue   # drive letter exists but no media inserted
        label, serial = _get_volume_info(letter)
        drives.append(DriveInfo(
            letter=letter,
            label=label,
            total_bytes=total,
            free_bytes=free,
            drive_type=dtype,
            serial=serial,
        ))
    return drives


def verify_drive_identity(letter: str, expected_serial: int) -> bool:
    """Return True only if the drive at `letter` still has the expected serial.

    Use this immediately before destructive operations to detect drive swaps
    (the user yanked one stick and inserted another between confirming and
    formatting, and Windows reused the drive letter).
    """
    if sys.platform != "win32":
        return True
    _, current = _get_volume_info(letter)
    return current == expected_serial and current != 0
