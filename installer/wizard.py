"""Wizard pages for the installer.

Five steps:
  1. Welcome — explains what's about to happen
  2. Drive — pick a removable drive
  3. API key — enter and optionally encrypt
  4. Options — confirm settings, optionally register autorun
  5. Install — progress bar
  6. Finish — success screen with launch button
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
    QWizard, QWizardPage,
)

from . import payload
from .drives import DriveInfo, list_removable_drives
from .install_worker import InstallPlan, InstallWorker
from .wipe import WipeWorker


# Minimum disk space we'll let the user proceed with (50 MB).
MIN_FREE_BYTES = 50 * 1024 * 1024


WIZARD_STYLE = """
QWizard, QWizardPage { background: #1E1F23; }
QLabel { color: #E6E6E6; font-size: 14px; }
QLabel#title { font-size: 20px; font-weight: 600; color: #FFFFFF; }
QLabel#subtitle { font-size: 13px; color: #A9B1BD; }
QLabel#body { font-size: 14px; color: #C8CED8; line-height: 1.5; }
QLineEdit, QComboBox {
    background: #2A2C32; color: #E6E6E6; border: 1px solid #3A3D45;
    border-radius: 8px; padding: 8px 12px; font-size: 14px;
}
QLineEdit:focus, QComboBox:focus { border-color: #6B8AFF; }
QComboBox::drop-down { border: none; width: 24px; }
QCheckBox { color: #E6E6E6; font-size: 13px; spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border: 1px solid #3A3D45;
    border-radius: 4px; background: #2A2C32;
}
QCheckBox::indicator:checked { background: #4F6BED; border-color: #4F6BED; }
QPushButton {
    background: #2A2C32; color: #E6E6E6; border: 1px solid #3A3D45;
    border-radius: 8px; padding: 8px 18px; font-size: 13px; min-width: 80px;
}
QPushButton:hover { background: #353841; }
QPushButton:default { background: #4F6BED; border: none; color: white; }
QPushButton:default:hover { background: #6079F0; }
QProgressBar {
    background: #2A2C32; border: 1px solid #3A3D45; border-radius: 8px;
    text-align: center; color: #E6E6E6; font-size: 12px; height: 24px;
}
QProgressBar::chunk { background: #4F6BED; border-radius: 7px; }
"""


class WelcomePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle(" ")  # blank — we draw our own title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(16)

        title = QLabel("Install ScreenSense to a USB drive")
        title.setObjectName("title")
        subtitle = QLabel("A portable screen-reading assistant powered by Claude.")
        subtitle.setObjectName("subtitle")

        body = QLabel(
            "This installer will:\n\n"
            "    •  Copy ScreenSense.exe to your USB drive\n"
            "    •  Optionally wipe the drive clean first (with confirmation)\n"
            "    •  Set up your Anthropic API key (with optional encryption)\n"
            "    •  Configure the AutoPlay popup so Windows offers to run it on insert\n"
            "    •  Optionally register true autorun (requires one-time admin)\n\n"
            "Once installed, ScreenSense gives you two hotkeys:\n"
            "    •  Ctrl+Shift+Space  →  ask Claude about anything on screen\n"
            "    •  Ctrl+Shift+Q       →  find the correct answer to a question\n"
            "        and highlight it with a high-contrast overlay\n\n"
            "Insert your USB drive now if you haven't already, then click Next."
        )
        body.setObjectName("body")
        body.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(12)
        layout.addWidget(body)
        layout.addStretch()

        if not payload.payload_available():
            warn = QLabel(
                "⚠ Installer payload missing — ScreenSense.exe was not bundled with "
                "this installer. The build is incomplete and installation will fail."
            )
            warn.setStyleSheet("color: #E89C4E; font-weight: 600;")
            warn.setWordWrap(True)
            layout.addWidget(warn)


class DrivePage(QWizardPage):
    """Pick a removable drive. Refreshes when user clicks Rescan."""

    def __init__(self):
        super().__init__()
        self.drives: list[DriveInfo] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(16)

        title = QLabel("Choose your USB drive")
        title.setObjectName("title")
        subtitle = QLabel(
            "Only removable drives are shown — your internal drives are safe."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        row = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.setMinimumHeight(40)
        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.refresh)
        row.addWidget(self.combo, 1)
        row.addWidget(rescan)

        self.status = QLabel("")
        self.status.setObjectName("body")
        self.status.setWordWrap(True)

        # Wipe option — gated behind a confirmation modal on Next.
        self.wipe_chk = QCheckBox(
            "Erase everything on this drive first  (quick format)"
        )
        self.wipe_chk.setChecked(False)
        self.wipe_chk.toggled.connect(self._on_wipe_toggled)

        self.wipe_warn = QLabel("")
        self.wipe_warn.setWordWrap(True)
        self.wipe_warn.setStyleSheet(
            "color: #E89C4E; background: rgba(232,156,78,0.08); "
            "border: 1px solid rgba(232,156,78,0.3); border-radius: 8px; "
            "padding: 10px; font-size: 12px;"
        )
        self.wipe_warn.hide()

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)
        layout.addLayout(row)
        layout.addWidget(self.status)
        layout.addSpacing(12)
        layout.addWidget(self.wipe_chk)
        layout.addWidget(self.wipe_warn)
        layout.addStretch()

        # Auto-refresh on page load
        QTimer.singleShot(0, self.refresh)
        self.combo.currentIndexChanged.connect(self._validate)

        self.registerField("drive_letter*", self.combo, "currentText")
        self.registerField("wipe_drive", self.wipe_chk)

    def _on_wipe_toggled(self, checked: bool) -> None:
        if checked:
            d = self.selected_drive()
            label = d.label or "(no label)" if d else ""
            letter = d.letter if d else "?"
            self.wipe_warn.setText(
                f"⚠ This will erase ALL files currently on drive {letter}:\\ "
                f"({label}). You'll be asked to confirm by typing the drive "
                f"letter before anything is deleted."
            )
            self.wipe_warn.show()
        else:
            self.wipe_warn.hide()

    def refresh(self) -> None:
        self.drives = list_removable_drives()
        self.combo.clear()
        if not self.drives:
            self.combo.addItem("No removable drives detected")
            self.combo.setEnabled(False)
            self.status.setText("Insert a USB drive and click Rescan.")
        else:
            self.combo.setEnabled(True)
            for d in self.drives:
                self.combo.addItem(d.display_name, userData=d)
            self.status.setText("")
        self._validate()

    def _validate(self) -> None:
        d = self.selected_drive()
        if d and d.free_bytes < MIN_FREE_BYTES:
            self.status.setText(
                f"⚠ This drive has only {d.free_bytes // (1024*1024)} MB free. "
                f"At least 50 MB recommended."
            )
            self.status.setStyleSheet("color: #E89C4E;")
        else:
            self.status.setText("")
        # If wipe is enabled, refresh the warning to match the newly selected drive.
        if self.wipe_chk.isChecked():
            self._on_wipe_toggled(True)
        self.completeChanged.emit()

    def selected_drive(self) -> Optional[DriveInfo]:
        if not self.drives:
            return None
        idx = self.combo.currentIndex()
        if idx < 0:
            return None
        return self.combo.itemData(idx)

    def isComplete(self) -> bool:
        d = self.selected_drive()
        return d is not None and d.free_bytes >= MIN_FREE_BYTES


class ApiKeyPage(QWizardPage):
    """Pick a provider, enter its API key, optionally encrypt."""

    PROVIDER_INFO = {
        "anthropic": {
            "display": "Anthropic Claude",
            "key_url": "console.anthropic.com → Settings → API Keys",
            "key_format": "sk-ant-…",
            "validator": lambda k: k.startswith("sk-ant-"),
        },
        "openai": {
            "display": "OpenAI ChatGPT",
            "key_url": "platform.openai.com → API Keys",
            "key_format": "sk-…  or  sk-proj-…",
            "validator": lambda k: k.startswith("sk-"),
        },
        "google": {
            "display": "Google Gemini",
            "key_url": "aistudio.google.com → Get API key",
            "key_format": "AIza…  or any Google AI Studio key",
            "validator": lambda k: len(k) >= 20,
        },
    }

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(14)

        title = QLabel("Pick your AI provider")
        title.setObjectName("title")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)

        # Provider radio buttons styled as a row of cards
        from PySide6.QtWidgets import QRadioButton, QButtonGroup, QHBoxLayout
        self.provider_group = QButtonGroup(self)
        provider_row = QHBoxLayout()
        for pid, info in self.PROVIDER_INFO.items():
            rb = QRadioButton(info["display"])
            rb.setProperty("provider_id", pid)
            rb.toggled.connect(self._on_provider_change)
            provider_row.addWidget(rb)
            self.provider_group.addButton(rb)

        # Default to Anthropic
        self.provider_group.buttons()[0].setChecked(True)
        self._current_pid = "anthropic"

        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.textChanged.connect(self._validate)

        self.show_key = QCheckBox("Show key")
        self.show_key.toggled.connect(
            lambda on: self.key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )

        self.encrypt_chk = QCheckBox(
            "Encrypt key with a passphrase  (strongly recommended for USB drives)"
        )
        self.encrypt_chk.setChecked(True)
        self.encrypt_chk.toggled.connect(self._on_encrypt_toggle)

        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setPlaceholderText("Passphrase")
        self.pass_input.textChanged.connect(self._validate)

        self.pass_confirm = QLineEdit()
        self.pass_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_confirm.setPlaceholderText("Confirm passphrase")
        self.pass_confirm.textChanged.connect(self._validate)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #E89C4E; font-size: 12px;")
        self.status.setWordWrap(True)

        layout.addWidget(title)
        layout.addLayout(provider_row)
        layout.addSpacing(4)
        layout.addWidget(self.subtitle)
        layout.addSpacing(8)
        layout.addWidget(QLabel("API key:"))
        layout.addWidget(self.key_input)
        layout.addWidget(self.show_key)
        layout.addSpacing(8)
        layout.addWidget(self.encrypt_chk)
        layout.addWidget(self.pass_input)
        layout.addWidget(self.pass_confirm)
        layout.addWidget(self.status)
        layout.addStretch()

        # Hidden field tracking the chosen provider — read by InstallPage
        self._provider_field = QLineEdit()
        self._provider_field.setText("anthropic")
        self._provider_field.hide()
        layout.addWidget(self._provider_field)

        self.registerField("provider*", self._provider_field)
        self.registerField("api_key*", self.key_input)
        self.registerField("passphrase", self.pass_input)
        self.registerField("use_encryption", self.encrypt_chk)

        # Apply initial defaults
        self._refresh_provider_ui()

    def _on_provider_change(self) -> None:
        for btn in self.provider_group.buttons():
            if btn.isChecked():
                self._current_pid = btn.property("provider_id")
                self._provider_field.setText(self._current_pid)
                break
        self._refresh_provider_ui()
        self._validate()

    def _refresh_provider_ui(self) -> None:
        info = self.PROVIDER_INFO[self._current_pid]
        self.subtitle.setText(f"Get an API key at: {info['key_url']}")
        self.key_input.setPlaceholderText(info["key_format"])

    def _on_encrypt_toggle(self, on: bool) -> None:
        self.pass_input.setEnabled(on)
        self.pass_confirm.setEnabled(on)
        if not on:
            self.pass_input.clear()
            self.pass_confirm.clear()
        self._validate()

    def _validate(self) -> None:
        key = self.key_input.text().strip()
        info = self.PROVIDER_INFO[self._current_pid]
        msg = ""
        if not key:
            msg = ""
        elif not info["validator"](key):
            msg = f"Key doesn't look like a typical {info['display']} key "
            msg += f"(expected {info['key_format']}). You can still proceed."
        elif self.encrypt_chk.isChecked():
            if not self.pass_input.text():
                msg = "Enter a passphrase, or uncheck encryption."
            elif len(self.pass_input.text()) < 6:
                msg = "Passphrase should be at least 6 characters."
            elif self.pass_input.text() != self.pass_confirm.text():
                msg = "Passphrases don't match."
        self.status.setText(msg)
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        key = self.key_input.text().strip()
        if not key:
            return False
        if self.encrypt_chk.isChecked():
            if len(self.pass_input.text()) < 6:
                return False
            if self.pass_input.text() != self.pass_confirm.text():
                return False
        return True


class OptionsPage(QWizardPage):
    """Final options before install: autorun, model choice.

    Model list is populated based on the provider chosen on the previous page.
    """

    # Per-provider model catalogs. Mirrors providers.PROVIDERS but inline so
    # the installer doesn't need to import from the screensense package.
    PROVIDER_MODELS = {
        "anthropic": [
            ("claude-sonnet-4-6",  "Claude Sonnet 4.6  (recommended — balanced)"),
            ("claude-opus-4-7",    "Claude Opus 4.7  (most capable, higher cost)"),
            ("claude-haiku-4-5",   "Claude Haiku 4.5  (fastest, cheapest)"),
        ],
        "openai": [
            ("gpt-5.4",            "GPT-5.4  (recommended — balanced)"),
            ("gpt-5.5",            "GPT-5.5  (most capable, higher cost)"),
            ("gpt-5.4-mini",       "GPT-5.4 mini  (cheaper, fast)"),
            ("gpt-5.4-nano",       "GPT-5.4 nano  (cheapest)"),
        ],
        "google": [
            ("gemini-3-flash-preview", "Gemini 3 Flash  (recommended — balanced)"),
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro  (most capable)"),
            ("gemini-2.5-flash",       "Gemini 2.5 Flash  (stable, no preview required)"),
            ("gemini-2.5-pro",         "Gemini 2.5 Pro  (stable, no preview required)"),
            ("gemini-2.5-flash-lite",  "Gemini 2.5 Flash-Lite  (cheapest stable)"),
        ],
    }

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(14)

        title = QLabel("Final options")
        title.setObjectName("title")

        layout.addWidget(title)
        layout.addSpacing(8)

        self.model_label = QLabel("Default model:")
        layout.addWidget(self.model_label)
        self.model_combo = QComboBox()
        layout.addWidget(self.model_combo)

        layout.addSpacing(12)

        self.autorun_chk = QCheckBox(
            "Register true autorun on this PC  (requires admin — runs a UAC prompt)"
        )
        self.autorun_chk.setChecked(False)
        layout.addWidget(self.autorun_chk)

        info = QLabel(
            "Without this, the USB drive shows up in Windows' AutoPlay popup when "
            "inserted (one click to launch). With it, ScreenSense launches silently "
            "every time the drive is plugged in — but only on this PC, and only if "
            "you click \"Yes\" on the UAC prompt during install.\n\n"
            "You can run Install-Autorun.bat from the USB drive later on any PC "
            "where you want silent autorun."
        )
        info.setObjectName("body")
        info.setWordWrap(True)
        info.setStyleSheet("color: #A9B1BD; font-size: 12px;")
        layout.addWidget(info)
        layout.addStretch()

        self.registerField("model", self.model_combo, "currentText")
        self.registerField("register_autorun", self.autorun_chk)

    def initializePage(self) -> None:
        """Populate model list based on the provider chosen on the previous page."""
        pid = self.field("provider") or "anthropic"
        models = self.PROVIDER_MODELS.get(pid, self.PROVIDER_MODELS["anthropic"])
        self.model_combo.clear()
        for model_id, label in models:
            self.model_combo.addItem(label, model_id)
        # Update label too — looks more polished
        provider_names = {
            "anthropic": "Claude", "openai": "ChatGPT", "google": "Gemini",
        }
        self.model_label.setText(f"Default {provider_names.get(pid, '')} model:")


class WipeConfirmDialog(QDialog):
    """Type-to-confirm dialog. Prevents click-through-everything mistakes."""

    def __init__(self, drive: DriveInfo, parent=None):
        super().__init__(parent)
        self.drive = drive
        self.setWindowTitle("Confirm drive wipe")
        self.setStyleSheet(WIZARD_STYLE + "QDialog { background: #1E1F23; }")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        warn = QLabel(f"You're about to erase EVERYTHING on this drive:")
        warn.setStyleSheet("color: #E85C5C; font-size: 16px; font-weight: 600;")

        label = self.drive.label or "(no label)"
        gb = self.drive.total_bytes / (1024**3)
        details = QLabel(
            f"    Drive: {self.drive.letter}:\\\n"
            f"    Label: {label}\n"
            f"    Size: {gb:.1f} GB\n"
            f"    Volume serial: {self.drive.serial:08X}"
        )
        details.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px; padding: 12px; background: #2A2C32; border-radius: 8px;")

        body = QLabel(
            f"This cannot be undone. All files currently on the drive will be deleted.\n\n"
            f"To confirm, type  {self.drive.letter}  below:"
        )
        body.setWordWrap(True)

        self.confirm_input = QLineEdit()
        self.confirm_input.setPlaceholderText(f"Type '{self.drive.letter}' to confirm")
        self.confirm_input.textChanged.connect(self._update_button)

        self.btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.btns.rejected.connect(self.reject)
        self.wipe_btn = QPushButton(f"Erase drive {self.drive.letter}:")
        self.wipe_btn.setEnabled(False)
        self.wipe_btn.setStyleSheet(
            "QPushButton { background: #B83838; border: none; color: white; }"
            "QPushButton:hover:enabled { background: #D04444; }"
            "QPushButton:disabled { background: #3A2A2A; color: #806060; }"
        )
        self.wipe_btn.clicked.connect(self.accept)
        self.btns.addButton(self.wipe_btn, QDialogButtonBox.ButtonRole.DestructiveRole)

        layout.addWidget(warn)
        layout.addWidget(details)
        layout.addWidget(body)
        layout.addWidget(self.confirm_input)
        layout.addWidget(self.btns)

    def _update_button(self, text: str) -> None:
        # Case-insensitive, accepts "E" or "E:" or "E:\"
        cleaned = text.strip().rstrip(":\\").upper()
        self.wipe_btn.setEnabled(cleaned == self.drive.letter.upper())


class InstallPage(QWizardPage):
    """Performs the install with a progress bar. Auto-advances on success."""

    install_complete = Signal(Path)

    def __init__(self):
        super().__init__()
        self.installed_exe: Optional[Path] = None
        self._install_worker: Optional[InstallWorker] = None
        self._install_thread: Optional[QThread] = None
        self._wipe_worker: Optional[WipeWorker] = None
        self._wipe_thread: Optional[QThread] = None
        self._selected_drive: Optional[DriveInfo] = None
        self._done = False
        self._aborted = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(16)

        self.title = QLabel("Installing…")
        self.title.setObjectName("title")
        self.status = QLabel("Preparing…")
        self.status.setObjectName("body")

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)

        layout.addWidget(self.title)
        layout.addSpacing(8)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addStretch()

    def initializePage(self) -> None:
        # Pull selected drive from the previous page
        wizard: InstallerWizard = self.wizard()  # type: ignore
        drive_page: DrivePage = wizard.page(InstallerWizard.PAGE_DRIVE)  # type: ignore
        drive = drive_page.selected_drive()
        if not drive:
            self._fail("No drive selected.")
            return
        self._selected_drive = drive

        # If wipe was requested, run the confirmation dialog FIRST, before we
        # commit to anything destructive. If user cancels, send them back.
        wipe_requested = bool(self.field("wipe_drive"))
        if wipe_requested:
            confirm = WipeConfirmDialog(drive, parent=self.wizard())
            if confirm.exec() != QDialog.DialogCode.Accepted:
                # User cancelled. Bounce back to the drive page.
                self._aborted = True
                self.title.setText("Cancelled")
                self.status.setText("Drive wipe cancelled. Going back…")
                QTimer.singleShot(800, self.wizard().back)
                return
            # User confirmed — proceed with wipe, then install.
            self._start_wipe(drive)
        else:
            # No wipe requested — go straight to install.
            self._start_install()

    def _start_wipe(self, drive: DriveInfo) -> None:
        self.title.setText("Wiping drive…")
        self.status.setText(f"Formatting {drive.letter}:\\ — please wait.")
        self.progress.setRange(0, 0)   # indeterminate during format

        self._wipe_thread = QThread()
        self._wipe_worker = WipeWorker(drive)
        self._wipe_worker.moveToThread(self._wipe_thread)
        self._wipe_thread.started.connect(self._wipe_worker.run)
        self._wipe_worker.progress.connect(self.status.setText)
        self._wipe_worker.finished.connect(self._on_wipe_finished)
        self._wipe_worker.failed.connect(self._on_wipe_failed)
        self._wipe_worker.finished.connect(self._wipe_thread.quit)
        self._wipe_worker.failed.connect(self._wipe_thread.quit)
        self._wipe_thread.start()

    def _on_wipe_finished(self) -> None:
        # Format is done. Now proceed with the install.
        self.progress.setRange(0, 100)
        self._start_install()

    def _on_wipe_failed(self, msg: str) -> None:
        self._fail(f"Drive wipe failed: {msg}")

    def _start_install(self) -> None:
        drive = self._selected_drive
        if not drive:
            self._fail("Drive reference lost.")
            return

        self.title.setText("Installing…")
        self.progress.setRange(0, 100)

        provider = self.field("provider") or "anthropic"
        api_key = self.field("api_key").strip()
        use_enc = bool(self.field("use_encryption"))
        passphrase = self.field("passphrase") if use_enc else None

        # Pull the model ID from the combo's currentData. The "model" field is
        # registered as currentText (display label), but we want the underlying
        # model ID stored as itemData.
        wizard: InstallerWizard = self.wizard()  # type: ignore
        options_page: OptionsPage = wizard.page(InstallerWizard.PAGE_OPTIONS)  # type: ignore
        model = options_page.model_combo.currentData()
        if not model:
            # Fallback: provider's recommended default
            defaults = {
                "anthropic": "claude-sonnet-4-6",
                "openai": "gpt-5.4",
                "google": "gemini-3-flash-preview",
            }
            model = defaults.get(provider, "claude-sonnet-4-6")

        plan = InstallPlan(
            target_dir=drive.path,
            provider=provider,
            api_key=api_key,
            passphrase=passphrase,
            model=model,
            register_autorun=bool(self.field("register_autorun")),
        )

        self._install_thread = QThread()
        self._install_worker = InstallWorker(plan)
        self._install_worker.moveToThread(self._install_thread)
        self._install_thread.started.connect(self._install_worker.run)
        self._install_worker.progress.connect(self._on_progress)
        self._install_worker.finished.connect(self._on_finished)
        self._install_worker.failed.connect(self._fail)
        self._install_worker.finished.connect(self._install_thread.quit)
        self._install_worker.failed.connect(self._install_thread.quit)
        self._install_thread.start()

    def _on_progress(self, pct: int, text: str) -> None:
        self.progress.setValue(pct)
        self.status.setText(text)

    def _on_finished(self, exe_path: Path) -> None:
        self.installed_exe = exe_path
        self.title.setText("Installed.")
        self.status.setText(f"ScreenSense is ready at {exe_path}")
        self._done = True
        self.completeChanged.emit()
        QTimer.singleShot(800, self.wizard().next)

    def _fail(self, msg: str) -> None:
        self.title.setText("Install failed")
        self.status.setText(msg)
        self.status.setStyleSheet("color: #E85C5C;")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

    def isComplete(self) -> bool:
        return self._done


class FinishPage(QWizardPage):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(16)

        title = QLabel("All done")
        title.setObjectName("title")

        self.body = QLabel("")
        self.body.setObjectName("body")
        self.body.setWordWrap(True)

        self.launch_chk = QCheckBox("Launch ScreenSense now")
        self.launch_chk.setChecked(True)

        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(self.body)
        layout.addSpacing(8)
        layout.addWidget(self.launch_chk)
        layout.addStretch()

        self.registerField("launch_now", self.launch_chk)

    def initializePage(self) -> None:
        wizard: InstallerWizard = self.wizard()  # type: ignore
        install_page: InstallPage = wizard.page(InstallerWizard.PAGE_INSTALL)  # type: ignore
        exe = install_page.installed_exe
        if exe:
            self.body.setText(
                f"ScreenSense is installed at  {exe}\n\n"
                f"Insert the USB drive on any Windows PC and pick \"Run ScreenSense\" "
                f"from the AutoPlay popup, or double-click ScreenSense.exe from the "
                f"drive. Your API key is already configured."
            )


class InstallerWizard(QWizard):
    PAGE_WELCOME = 0
    PAGE_DRIVE = 1
    PAGE_API = 2
    PAGE_OPTIONS = 3
    PAGE_INSTALL = 4
    PAGE_FINISH = 5

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ScreenSense Installer")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnLastPage, True)
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)
        self.setStyleSheet(WIZARD_STYLE)
        self.resize(640, 460)

        self.setPage(self.PAGE_WELCOME, WelcomePage())
        self.setPage(self.PAGE_DRIVE, DrivePage())
        self.setPage(self.PAGE_API, ApiKeyPage())
        self.setPage(self.PAGE_OPTIONS, OptionsPage())
        self.setPage(self.PAGE_INSTALL, InstallPage())
        self.setPage(self.PAGE_FINISH, FinishPage())

        self.setButtonText(QWizard.WizardButton.NextButton, "Next →")
        self.setButtonText(QWizard.WizardButton.BackButton, "← Back")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Finish")

        self.finished.connect(self._on_finished)

    def _on_finished(self, result: int) -> None:
        if result != QWizard.DialogCode.Accepted:
            return
        if not self.field("launch_now"):
            return
        install_page: InstallPage = self.page(self.PAGE_INSTALL)  # type: ignore
        if install_page.installed_exe and install_page.installed_exe.exists():
            import subprocess
            try:
                subprocess.Popen([str(install_page.installed_exe)])
            except OSError:
                pass
