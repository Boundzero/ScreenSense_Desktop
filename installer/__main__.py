"""ScreenSense Installer — copies app to USB drive and configures API key."""
from __future__ import annotations

import sys
from PySide6.QtWidgets import QApplication

from .wizard import InstallerWizard


def main() -> int:
    app = QApplication(sys.argv)
    wizard = InstallerWizard()
    wizard.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
