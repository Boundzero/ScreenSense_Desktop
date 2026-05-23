"""Install worker — performs the actual file copy and config write.

Runs in a QThread so the wizard UI stays responsive. Emits progress updates
and a final success/failure signal.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from . import payload


@dataclass
class InstallPlan:
    target_dir: Path                # e.g. E:\
    provider: str                   # "anthropic" | "openai" | "google"
    api_key: str
    passphrase: Optional[str]       # if None, key stored plaintext
    model: str
    register_autorun: bool = False  # run Install-Autorun.bat after copy


class InstallWorker(QObject):
    progress = Signal(int, str)     # (percent 0-100, status text)
    finished = Signal(Path)         # path to installed ScreenSense.exe
    failed = Signal(str)

    def __init__(self, plan: InstallPlan):
        super().__init__()
        self.plan = plan

    def run(self) -> None:
        try:
            self._do_install()
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}")

    def _do_install(self) -> None:
        target = self.plan.target_dir

        # Wait for the drive to be available. Right after a format, Windows
        # briefly unmounts and remounts the volume — Path.exists() returns
        # False during that window even though the drive is physically present.
        # Poll for up to 30 seconds before giving up.
        if not self._wait_for_drive(target, timeout_s=30):
            raise FileNotFoundError(
                f"Drive {target} not available after waiting 30 seconds. "
                f"It may have been unplugged. Try inserting it again and re-running."
            )

        files = payload.payload_files()
        if not files:
            raise FileNotFoundError(
                "Installer payload missing. ScreenSense.exe was not bundled — "
                "the installer build is broken."
            )

        # Copy each payload file with progress reporting.
        total_bytes = sum(f.stat().st_size for f in files)
        copied_bytes = 0
        for src in files:
            dst = target / src.name
            self.progress.emit(
                int(copied_bytes / total_bytes * 80),
                f"Copying {src.name}…",
            )
            self._copy_with_progress(src, dst, copied_bytes, total_bytes)
            copied_bytes += src.stat().st_size

        self.progress.emit(85, "Writing configuration…")
        self._write_config(target)

        if self.plan.register_autorun:
            self.progress.emit(92, "Registering autorun task…")
            self._register_autorun(target)

        self.progress.emit(100, "Done.")
        self.finished.emit(target / "ScreenSense.exe")

    def _wait_for_drive(self, target: Path, timeout_s: float = 30.0) -> bool:
        """Poll until the drive is available for writing, or timeout.

        After a format, Windows briefly unmounts and remounts the volume.
        We check both that the path exists AND that we can list it (mount
        sometimes lags behind path resolution).
        """
        import time as _time
        deadline = _time.monotonic() + timeout_s
        attempt = 0
        while _time.monotonic() < deadline:
            attempt += 1
            try:
                if target.exists() and target.is_dir():
                    # Try a non-destructive read to confirm the volume is mounted.
                    list(target.iterdir())
                    if attempt > 1:
                        self.progress.emit(0, "Drive ready, starting install…")
                    return True
            except (OSError, PermissionError):
                pass
            self.progress.emit(0, f"Waiting for drive to come online… ({attempt})")
            _time.sleep(0.5)
        return False

    def _copy_with_progress(self, src: Path, dst: Path, base: int, total: int) -> None:
        """Copy with periodic progress updates — better UX than shutil.copy on big files."""
        size = src.stat().st_size
        chunk = 1024 * 1024  # 1 MB
        with src.open("rb") as fin, dst.open("wb") as fout:
            written = 0
            while True:
                data = fin.read(chunk)
                if not data:
                    break
                fout.write(data)
                written += len(data)
                pct = int((base + written) / total * 80)
                self.progress.emit(pct, f"Copying {src.name}… {written // (1024*1024)} MB")
        # preserve file mtime for cleanliness
        shutil.copystat(src, dst)

    def _write_config(self, target: Path) -> None:
        """Write screensense.json next to the executable, optionally encrypting the key.

        Uses the new multi-provider config schema. The user can add more providers
        later from the tray menu in the app.
        """
        config_data: dict = {
            "provider": self.plan.provider,
            "models": {self.plan.provider: self.plan.model},
            "keys_plaintext": {},
            "keys_encrypted": {},
            "hotkey": "ctrl+shift+space",
            "quiz_hotkey": "ctrl+shift+q",
            "region_hotkey": "ctrl+shift+r",
            "poll_fps": 2.0,
            "auto_suggest": True,
        }

        if self.plan.passphrase:
            import base64
            import os
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

            salt = os.urandom(16)
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000)
            key = base64.urlsafe_b64encode(kdf.derive(self.plan.passphrase.encode()))
            token = Fernet(key).encrypt(self.plan.api_key.encode()).decode()
            config_data["keys_encrypted"][self.plan.provider] = token
            config_data["salt_b64"] = base64.b64encode(salt).decode()
        else:
            config_data["keys_plaintext"][self.plan.provider] = self.plan.api_key

        (target / "screensense.json").write_text(json.dumps(config_data, indent=2))

    def _register_autorun(self, target: Path) -> None:
        """Run the bundled Install-Autorun.bat via UAC elevation."""
        bat = target / "Install-Autorun.bat"
        if not bat.exists() or sys.platform != "win32":
            return
        # ShellExecute with 'runas' verb triggers the UAC prompt
        try:
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "cmd.exe", f'/c "{bat}"', str(target), 1,
            )
        except Exception:
            pass  # non-fatal — user can run it manually later
