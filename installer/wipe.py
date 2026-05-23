"""Drive wipe (quick format).

Uses Windows' built-in `format` command via subprocess. We perform a quick
format only — not a secure erase. Quick format clears the file allocation
table; data is not securely overwritten. That's the right tradeoff for the
USB-app use case (we want to delete old junk, not defeat forensic recovery).

SAFETY: Never call this without first verifying via verify_drive_identity()
that the drive is still the one the user confirmed. The caller is also
responsible for double-confirming with the user before invocation.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .drives import DriveInfo, verify_drive_identity


class WipeWorker(QObject):
    """Runs the format command in a worker thread."""

    progress = Signal(str)   # status messages
    finished = Signal()
    failed = Signal(str)

    def __init__(self, drive: DriveInfo, new_label: str = "SCREENSENSE"):
        super().__init__()
        self.drive = drive
        self.new_label = new_label[:11]  # FAT/exFAT label limit

    def run(self) -> None:
        try:
            self._do_wipe()
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")

    def _do_wipe(self) -> None:
        if sys.platform != "win32":
            self.failed.emit("Drive wipe is only supported on Windows.")
            return

        # Re-verify drive identity right before issuing the destructive command.
        # If the user swapped drives between confirming and now (and Windows
        # reused the letter), this catches it.
        self.progress.emit("Verifying drive identity…")
        if not verify_drive_identity(self.drive.letter, self.drive.serial):
            self.failed.emit(
                f"Drive {self.drive.letter}: is no longer the drive you confirmed. "
                f"It may have been removed or swapped. Aborting for safety."
            )
            return

        # Final hard safety check: never format anything but a removable drive.
        # (verify_drive_identity already confirms serial, but defense in depth.)
        from .drives import _get_drive_type, DRIVE_REMOVABLE
        if _get_drive_type(self.drive.letter) != DRIVE_REMOVABLE:
            self.failed.emit(
                f"Drive {self.drive.letter}: is not a removable drive. "
                f"Refusing to format for safety."
            )
            return

        self.progress.emit(f"Formatting {self.drive.letter}:\\ …")

        # /Q = quick format, /Y = suppress confirmation prompt (we already got our own)
        # /V: = volume label, /FS:exFAT works for sticks > 32 GB and is widely
        # readable on Windows/macOS/Linux.
        # Choose FAT32 for sticks <= 32 GB (more universally compatible).
        fs = "FAT32" if self.drive.total_bytes <= 32 * 1024**3 else "exFAT"

        cmd = [
            "format",
            f"{self.drive.letter}:",
            "/Q",                       # quick format
            "/Y",                       # auto-confirm
            f"/FS:{fs}",                # filesystem
            f"/V:{self.new_label}",     # volume label
        ]

        try:
            # format.com requires stdin input even with /Y. Send a newline.
            result = subprocess.run(
                cmd,
                input="\n",
                capture_output=True,
                text=True,
                timeout=120,
                shell=True,   # `format` is a shell builtin/internal command on Windows
            )
        except subprocess.TimeoutExpired:
            self.failed.emit("Format timed out after 2 minutes.")
            return
        except OSError as e:
            self.failed.emit(f"Couldn't run format: {e}")
            return

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()[:300]
            self.failed.emit(f"Format failed (exit {result.returncode}): {err}")
            return

        self.progress.emit("Drive wiped successfully.")
        self.finished.emit()
