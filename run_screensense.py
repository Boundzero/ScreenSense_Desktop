"""Standalone entry point for the bundled ScreenSense executable.

This file exists separately from screensense/__main__.py because PyInstaller
flattens the package structure during bundling, which breaks relative imports
inside __main__.py. By using absolute imports here, we sidestep that issue.

When running from source, use:   python -m screensense
When building the .exe, PyInstaller uses this file as the entry point.
"""
from __future__ import annotations

import os
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

import datetime
import faulthandler
import sys
import traceback
from pathlib import Path


def _log_path() -> Path:
    """Where to write the log file. Next to the executable on USB if frozen,
    otherwise in the current working directory.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "screensense.log"
    return Path.cwd() / "screensense.log"


def _setup_logging() -> None:
    """Mirror stdout/stderr to a log file next to the executable.

    The .exe is built with console=False (no console window), which means
    print() and tracebacks normally vanish into the void. Capturing them
    to a file is the only way to diagnose crashes in the wild.

    Also enables Python's faulthandler so segfaults and access violations
    inside C extensions (e.g. onnxruntime, mss, Qt) get a Python-side
    stack trace before the process dies.
    """
    try:
        log = open(_log_path(), "a", buffering=1, encoding="utf-8", errors="replace")
        log.write(f"\n=== ScreenSense session start: "
                  f"{datetime.datetime.now().isoformat()} ===\n")

        class _Tee:
            def __init__(self, *streams):
                self.streams = streams
            def write(self, data):
                for s in self.streams:
                    try:
                        s.write(data)
                        s.flush()
                    except Exception:
                        pass
                return len(data)
            def flush(self):
                for s in self.streams:
                    try:
                        s.flush()
                    except Exception:
                        pass

        sys.stdout = _Tee(log, sys.__stdout__ or sys.stdout)
        sys.stderr = _Tee(log, sys.__stderr__ or sys.stderr)

        # Native crash reporter — writes a Python-side traceback to the log
        # before the process dies from a segfault or access violation. This
        # is what catches C-extension crashes that wouldn't otherwise be
        # diagnosable.
        faulthandler.enable(file=log)
    except Exception as e:
        print(f"[ScreenSense] logging setup failed: {e}", file=sys.stderr)


def _excepthook(exc_type, exc_value, exc_tb) -> None:
    """Catch unhandled Python exceptions and log them before dying."""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(f"[ScreenSense] UNCAUGHT EXCEPTION:\n{msg}", file=sys.stderr, flush=True)
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main() -> int:
    _setup_logging()
    sys.excepthook = _excepthook
    print(f"[ScreenSense] starting, log file: {_log_path()}", flush=True)

    from PySide6.QtWidgets import QApplication
    from screensense.app import ScreenSenseApp

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # we live in the system tray
    _ = ScreenSenseApp(app)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
