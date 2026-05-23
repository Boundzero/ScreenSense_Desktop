"""ScreenSense — portable screen-reading assistant powered by Claude."""
from __future__ import annotations

import sys
from PySide6.QtWidgets import QApplication

from .app import ScreenSenseApp


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # we live in the system tray
    _ = ScreenSenseApp(app)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
