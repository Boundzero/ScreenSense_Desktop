"""Standalone entry point for the bundled installer executable.

See run_screensense.py for rationale — PyInstaller and relative imports don't
play nicely, so we use absolute imports here.

When running from source, use:   python -m installer
When building the .exe, PyInstaller uses this file as the entry point.
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from installer.wizard import InstallerWizard


def main() -> int:
    app = QApplication(sys.argv)
    wizard = InstallerWizard()
    wizard.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
