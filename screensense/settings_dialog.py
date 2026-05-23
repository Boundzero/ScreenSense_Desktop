"""Settings dialog — custom hotkeys + Telegram output configuration.

Opened from the tray menu. Lets the user:
  - Rebind the three global hotkeys (ask / quiz / region)
  - Choose output mode (on-screen vs Telegram)
  - Configure and test a Telegram bot token + chat id
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .config import Config
from .ui import STYLE


# Map Qt key codes to the friendly hotkey token format our HotkeyListener
# understands (e.g. "ctrl+shift+space").
_MODIFIER_TOKENS = [
    (Qt.KeyboardModifier.ControlModifier, "ctrl"),
    (Qt.KeyboardModifier.AltModifier, "alt"),
    (Qt.KeyboardModifier.ShiftModifier, "shift"),
    # pynput's GlobalHotKeys expects <cmd> for the Windows/Super/Meta key,
    # not <win>. Using the right token here keeps the listener able to bind it.
    (Qt.KeyboardModifier.MetaModifier, "cmd"),
]

# Qt.Key values that should be spelled out as multi-char names.
_NAMED_KEYS = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_PageUp: "page_up",
    Qt.Key.Key_PageDown: "page_down",
}


class HotkeyEdit(QLineEdit):
    """A line edit that captures a key chord when focused and shows it as text.

    The user clicks it and presses the desired combination; we translate the
    Qt event into our friendly hotkey string. They can also type it manually.
    """

    def __init__(self, initial: str = "", parent=None):
        super().__init__(initial, parent)
        self.setPlaceholderText("Click and press a key combination…")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        # Ignore lone modifier presses — wait for a real key.
        if key in (
            Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
            Qt.Key.Key_AltGr, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R,
        ):
            return

        mods = event.modifiers()
        parts: list[str] = []
        for flag, token in _MODIFIER_TOKENS:
            if mods & flag:
                parts.append(token)

        # Resolve the main key. IMPORTANT: do NOT use event.text() to get the
        # base character — when a modifier like Ctrl is held, event.text()
        # returns a control character (e.g. '\x01' for Ctrl+A), not 'a', which
        # fails isprintable() and silently drops the capture. Resolve the base
        # key from the Qt key code directly instead.
        base: str = ""
        if key in _NAMED_KEYS:
            base = _NAMED_KEYS[key]
        elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35:
            base = f"f{key - Qt.Key.Key_F1 + 1}"
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            base = chr(ord("a") + (key - Qt.Key.Key_A))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            base = chr(ord("0") + (key - Qt.Key.Key_0))
        else:
            # Fall back to event.text() for punctuation etc., but only if it's
            # a normal printable single char (no modifier mangling).
            text = event.text().strip().lower()
            if text and text.isprintable() and len(text) == 1:
                base = text
            else:
                return  # unsupported key — ignore

        parts.append(base)
        self.setText("+".join(parts))


class SettingsDialog(QDialog):
    """Edit hotkeys and output settings. Reads/writes the passed-in Config."""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("ScreenSense — Settings")
        self.setStyleSheet(
            STYLE
            + "QDialog { background: #1E1F23; }"
            + "QGroupBox { color: #E6E6E6; border: 1px solid #3A3D45;"
            + " border-radius: 8px; margin-top: 10px; padding-top: 10px; }"
            + "QGroupBox::title { subcontrol-origin: margin; left: 10px;"
            + " padding: 0 4px; }"
            + "QLabel { color: #E6E6E6; }"
            + "QComboBox { background: #2A2C32; border: 1px solid #3A3D45;"
            + " border-radius: 6px; padding: 6px 10px; }"
        )
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        # ---- Hotkeys group ----
        hk_group = QGroupBox("Hotkeys")
        hk_form = QFormLayout(hk_group)
        self.ask_edit = HotkeyEdit(config.hotkey)
        self.quiz_edit = HotkeyEdit(config.quiz_hotkey)
        self.region_edit = HotkeyEdit(config.region_hotkey)
        self.region_quiz_edit = HotkeyEdit(config.region_quiz_hotkey)
        hk_form.addRow("Ask about screen:", self.ask_edit)
        hk_form.addRow("Quiz mode (full screen):", self.quiz_edit)
        hk_form.addRow("Ask about a region:", self.region_edit)
        hk_form.addRow("Quiz mode on a region:", self.region_quiz_edit)
        hk_hint = QLabel(
            "Click a field and press your desired combination, or type it "
            "(e.g. ctrl+shift+space). Changes apply when you click OK."
        )
        hk_hint.setWordWrap(True)
        hk_hint.setStyleSheet("color: #A9B1BD; font-size: 11px;")
        hk_form.addRow("", hk_hint)
        layout.addWidget(hk_group)

        # ---- Output group ----
        out_group = QGroupBox("Output")
        out_layout = QVBoxLayout(out_group)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Send answers to:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("On-screen (default)", "screen")
        self.mode_combo.addItem("Telegram (nothing shows on screen)", "telegram")
        self.mode_combo.setCurrentIndex(0 if config.output_mode != "telegram" else 1)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_change)
        mode_row.addWidget(self.mode_combo, 1)
        out_layout.addLayout(mode_row)

        # Telegram sub-fields (shown only in Telegram mode)
        self.tg_widget = QWidget()
        tg_form = QFormLayout(self.tg_widget)
        tg_form.setContentsMargins(0, 8, 0, 0)
        self.token_input = QLineEdit(config.telegram_token)
        self.token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_input.setPlaceholderText("123456:ABC-DEF… (from @BotFather)")
        tg_form.addRow("Bot token:", self.token_input)

        chat_row = QWidget()
        chat_h = QHBoxLayout(chat_row)
        chat_h.setContentsMargins(0, 0, 0, 0)
        self.chat_input = QLineEdit(config.telegram_chat_id)
        self.chat_input.setPlaceholderText("e.g. 123456789")
        chat_h.addWidget(self.chat_input, 1)
        self.fetch_chat_btn = QPushButton("Auto-detect")
        self.fetch_chat_btn.clicked.connect(self._fetch_chat_id)
        chat_h.addWidget(self.fetch_chat_btn)
        tg_form.addRow("Chat ID:", chat_row)

        test_row = QWidget()
        test_h = QHBoxLayout(test_row)
        test_h.setContentsMargins(0, 0, 0, 0)
        self.test_btn = QPushButton("Send test message")
        self.test_btn.clicked.connect(self._send_test)
        test_h.addWidget(self.test_btn)
        self.tg_status = QLabel("")
        self.tg_status.setStyleSheet("color: #A9B1BD; font-size: 11px;")
        self.tg_status.setWordWrap(True)
        test_h.addWidget(self.tg_status, 1)
        tg_form.addRow("", test_row)

        tg_help = QLabel(
            "Setup: message @BotFather to create a bot and get the token, then "
            "send any message to your new bot so it can reply to you. Use "
            "Auto-detect to fill the chat ID. In Telegram mode, answers go only "
            "to Telegram — nothing appears on screen (errors still show)."
        )
        tg_help.setWordWrap(True)
        tg_help.setStyleSheet("color: #A9B1BD; font-size: 11px;")
        tg_form.addRow("", tg_help)

        out_layout.addWidget(self.tg_widget)
        layout.addWidget(out_group)

        # ---- OK / Cancel ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_mode_change()  # set initial visibility

    def _on_mode_change(self, *_args) -> None:
        is_tg = self.mode_combo.currentData() == "telegram"
        self.tg_widget.setVisible(is_tg)

    def _fetch_chat_id(self) -> None:
        from .telegram import fetch_recent_chat_id
        token = self.token_input.text().strip()
        if not token:
            self.tg_status.setText("Enter the bot token first.")
            self.tg_status.setStyleSheet("color: #E85C5C; font-size: 11px;")
            return
        self.tg_status.setText("Looking for recent messages…")
        try:
            cid = fetch_recent_chat_id(token)
            self.chat_input.setText(cid)
            self.tg_status.setText(f"Found chat ID {cid}.")
            self.tg_status.setStyleSheet("color: #56B25A; font-size: 11px;")
        except Exception as e:
            self.tg_status.setText(str(e))
            self.tg_status.setStyleSheet("color: #E85C5C; font-size: 11px;")

    def _send_test(self) -> None:
        from .telegram import send_to_telegram
        token = self.token_input.text().strip()
        chat_id = self.chat_input.text().strip()
        if not token or not chat_id:
            self.tg_status.setText("Enter both token and chat ID first.")
            self.tg_status.setStyleSheet("color: #E85C5C; font-size: 11px;")
            return
        self.tg_status.setText("Sending…")
        try:
            send_to_telegram(token, chat_id, "✅ ScreenSense test message — your Telegram output is working.")
            self.tg_status.setText("Test message sent — check Telegram.")
            self.tg_status.setStyleSheet("color: #56B25A; font-size: 11px;")
        except Exception as e:
            self.tg_status.setText(str(e))
            self.tg_status.setStyleSheet("color: #E85C5C; font-size: 11px;")

    def _accept(self) -> None:
        # Validate hotkeys are non-empty
        ask = self.ask_edit.text().strip()
        quiz = self.quiz_edit.text().strip()
        region = self.region_edit.text().strip()
        region_quiz = self.region_quiz_edit.text().strip()
        if not (ask and quiz and region and region_quiz):
            QMessageBox.warning(self, "Missing hotkey",
                                "All four hotkeys must be set.")
            return
        # Validate they're distinct
        if len({ask, quiz, region, region_quiz}) != 4:
            QMessageBox.warning(self, "Duplicate hotkeys",
                                "Each action needs a different hotkey.")
            return

        mode = self.mode_combo.currentData()
        if mode == "telegram":
            if not self.token_input.text().strip() or not self.chat_input.text().strip():
                QMessageBox.warning(
                    self, "Telegram not configured",
                    "Enter a bot token and chat ID, or switch back to on-screen output.",
                )
                return

        # Persist
        self.config.hotkey = ask
        self.config.quiz_hotkey = quiz
        self.config.region_hotkey = region
        self.config.region_quiz_hotkey = region_quiz
        self.config.output_mode = mode
        self.config.telegram_token = self.token_input.text().strip()
        self.config.telegram_chat_id = self.chat_input.text().strip()
        self.config.save()
        self.accept()
