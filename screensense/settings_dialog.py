"""Settings dialog — custom hotkeys + Telegram output configuration.

Opened from the tray menu. Lets the user:
  - Rebind the three global hotkeys (ask / quiz / region)
  - Choose output mode (on-screen vs Telegram)
  - Configure and test a Telegram bot token + chat id
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPixmap, QImage
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
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


class MouseHotkeyEdit(QLineEdit):
    """A line edit that captures a mouse-button chord when focused.

    The user clicks it (keyboard focus arrives via Tab or an initial click
    inside the field, which we treat specially — a bare left-click on an
    empty field is used for focus and does NOT capture). Then they press
    the desired mouse button (optionally while holding Ctrl/Shift/Alt/Cmd)
    and we translate the chord into our mouse hotkey format.

    Supported mouse buttons: side back (XButton1), side forward (XButton2),
    and middle (scroll wheel click). Left and right clicks are intentionally
    not accepted — binding them would make every click fire ScreenSense.
    """

    def __init__(self, initial: str = "", parent=None):
        super().__init__(initial, parent)
        self.setPlaceholderText("Click, then press a mouse button (side or middle)…")
        # Tracks whether the user has clicked INTO the field (gaining focus)
        # or is now clicking a mouse button they want to capture. The first
        # left-click lands focus; subsequent clicks are capture attempts.
        self._focused_at_least_once = False

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._focused_at_least_once = True

    def mousePressEvent(self, event: QMouseEvent) -> None:
        btn = event.button()
        mods = event.modifiers()

        # Left-click on an unfocused field: just take focus, don't capture.
        # (Users need some way to activate the field without immediately
        # binding their left mouse button.)
        if btn == Qt.MouseButton.LeftButton and not self.hasFocus():
            super().mousePressEvent(event)
            return

        # Right-click: always ignore (would conflict with context menus).
        if btn == Qt.MouseButton.RightButton:
            super().mousePressEvent(event)
            return

        # Resolve the mouse-button token.
        token: str = ""
        if btn == Qt.MouseButton.MiddleButton:
            token = "middle_click"
        elif btn == Qt.MouseButton.BackButton:
            token = "mouse_button_4"
        elif btn == Qt.MouseButton.ForwardButton:
            token = "mouse_button_5"
        elif btn == Qt.MouseButton.LeftButton and self.hasFocus():
            # Second left-click after focus: still treat as a focus event,
            # don't bind left-click.
            super().mousePressEvent(event)
            return
        else:
            # Any other mouse button: ignore.
            super().mousePressEvent(event)
            return

        # Build the chord string with any modifiers held at press time.
        parts: list[str] = []
        for flag, modtok in _MODIFIER_TOKENS:
            if mods & flag:
                parts.append(modtok)
        parts.append(token)
        self.setText("+".join(parts))
        event.accept()


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
        self.setMinimumWidth(540)

        # Outer dialog layout: scroll area + fixed button bar.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable container for all the sections. The content widget's
        # own layout is called `layout` (the name the rest of __init__ uses)
        # so the section-building code below reads unchanged.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background: #1E1F23; }"
            "QScrollBar:vertical { background: #1E1F23; width: 10px; margin: 0; }"
            "QScrollBar::handle:vertical { background: #3A3D45; border-radius: 5px;"
            " min-height: 20px; }"
            "QScrollBar::handle:vertical:hover { background: #4A4E57; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }"
        )
        content = QWidget()
        content.setStyleSheet("background: #1E1F23;")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 12, 12, 12)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

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

        # ---- Mouse hotkeys group (optional, additional triggers) ----
        mh_group = QGroupBox("Mouse hotkeys (optional)")
        mh_form = QFormLayout(mh_group)
        self.ask_mouse_edit = MouseHotkeyEdit(config.hotkey_mouse)
        self.quiz_mouse_edit = MouseHotkeyEdit(config.quiz_hotkey_mouse)
        self.region_mouse_edit = MouseHotkeyEdit(config.region_hotkey_mouse)
        self.region_quiz_mouse_edit = MouseHotkeyEdit(config.region_quiz_hotkey_mouse)
        # Each row is the edit field + a small Clear button so the user can
        # unbind a mouse hotkey without having to delete the text manually.
        for label, edit in [
            ("Ask about screen:", self.ask_mouse_edit),
            ("Quiz mode (full screen):", self.quiz_mouse_edit),
            ("Ask about a region:", self.region_mouse_edit),
            ("Quiz mode on a region:", self.region_quiz_mouse_edit),
        ]:
            row_w = QWidget()
            row_h = QHBoxLayout(row_w)
            row_h.setContentsMargins(0, 0, 0, 0)
            row_h.addWidget(edit, 1)
            clear_btn = QPushButton("Clear")
            clear_btn.setFixedWidth(60)
            clear_btn.clicked.connect(edit.clear)
            row_h.addWidget(clear_btn)
            mh_form.addRow(label, row_w)
        mh_hint = QLabel(
            "Click a field, then press a side mouse button (back/forward) or "
            "middle-click, optionally while holding Ctrl/Shift/Alt. Leave empty "
            "to disable. Use Clear to unbind."
        )
        mh_hint.setWordWrap(True)
        mh_hint.setStyleSheet("color: #A9B1BD; font-size: 11px;")
        mh_form.addRow("", mh_hint)
        layout.addWidget(mh_group)

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
        self.token_input.textChanged.connect(
            lambda _=None: self._update_rt_help() if hasattr(self, "rt_help") else None
        )
        tg_form.addRow("Bot token:", self.token_input)

        chat_row = QWidget()
        chat_h = QHBoxLayout(chat_row)
        chat_h.setContentsMargins(0, 0, 0, 0)
        self.chat_input = QLineEdit(config.telegram_chat_id)
        self.chat_input.setPlaceholderText("e.g. 123456789")
        self.chat_input.textChanged.connect(
            lambda _=None: self._update_rt_help() if hasattr(self, "rt_help") else None
        )
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

        # ---- Remote Trigger server ----
        rt_group = QGroupBox("Remote trigger (phone / tablet / browser)")
        rt_layout = QVBoxLayout(rt_group)

        self.rt_enable = QCheckBox(
            "Enable remote trigger server (serve a web page on the LAN)"
        )
        self.rt_enable.setChecked(config.trigger_server_enabled)
        self.rt_enable.toggled.connect(self._on_rt_toggle)
        rt_layout.addWidget(self.rt_enable)

        # Everything that only matters when enabled lives in rt_widget so we
        # can hide it as a group.
        self.rt_widget = QWidget()
        rt_inner = QVBoxLayout(self.rt_widget)
        rt_inner.setContentsMargins(0, 8, 0, 0)

        # Port
        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Port:"))
        self.rt_port = QSpinBox()
        self.rt_port.setRange(1024, 65535)
        self.rt_port.setValue(int(config.trigger_server_port or 7474))
        self.rt_port.valueChanged.connect(self._refresh_rt_url)
        port_row.addWidget(self.rt_port)
        port_row.addStretch()
        rt_inner.addLayout(port_row)

        # Token row with regenerate button
        tok_row = QHBoxLayout()
        tok_row.addWidget(QLabel("Access token:"))
        self.rt_token = QLineEdit(config.trigger_server_token)
        self.rt_token.setReadOnly(True)
        self.rt_token.setPlaceholderText("(generated when you enable)")
        tok_row.addWidget(self.rt_token, 1)
        regen_btn = QPushButton("Regenerate")
        regen_btn.clicked.connect(self._regenerate_token)
        tok_row.addWidget(regen_btn)
        rt_inner.addLayout(tok_row)

        # URL display
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Bookmark URL:"))
        self.rt_url_display = QLineEdit("")
        self.rt_url_display.setReadOnly(True)
        url_row.addWidget(self.rt_url_display, 1)
        copy_btn = QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_url)
        url_row.addWidget(copy_btn)
        rt_inner.addLayout(url_row)

        # QR code display
        self.rt_qr_label = QLabel("")
        self.rt_qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rt_qr_label.setStyleSheet("background: white; padding: 8px; border-radius: 8px;")
        rt_inner.addWidget(self.rt_qr_label)

        # Help / warning text
        self.rt_help = QLabel("")
        self.rt_help.setWordWrap(True)
        self.rt_help.setStyleSheet("color: #A9B1BD; font-size: 11px;")
        rt_inner.addWidget(self.rt_help)

        rt_layout.addWidget(self.rt_widget)
        layout.addWidget(rt_group)

        # Initial state
        self._refresh_rt_url()
        self._on_rt_toggle()

        # ---- OK / Cancel (fixed at the bottom, outside the scroll area) ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        # Pad the button bar so it visually matches the scrollable content.
        button_wrap = QWidget()
        button_wrap.setStyleSheet("background: #1E1F23;")
        bw = QVBoxLayout(button_wrap)
        bw.setContentsMargins(12, 8, 12, 12)
        bw.addWidget(buttons)
        outer.addWidget(button_wrap)

        self._on_mode_change()  # set initial visibility

        # Size the dialog: ideally wide enough for all content, tall enough
        # to show most sections without scrolling — but never taller than
        # ~85% of the screen's available height so OK/Cancel always visible.
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_h = int(avail.height() * 0.85)
            # Default to a sensible 720px height, capped at screen max.
            self.resize(560, min(720, max_h))
            self.setMaximumHeight(max_h)

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

    def _on_rt_toggle(self, *_args) -> None:
        """Show/hide the inner config, and auto-generate a token on first enable."""
        enabled = self.rt_enable.isChecked()
        self.rt_widget.setVisible(enabled)
        if enabled and not self.rt_token.text().strip():
            from .trigger_server import generate_token
            self.rt_token.setText(generate_token())
            self._refresh_rt_url()
        self._update_rt_help()

    def _regenerate_token(self) -> None:
        from .trigger_server import generate_token
        resp = QMessageBox.question(
            self, "Regenerate token?",
            "A new token will be generated and the old bookmarks on your "
            "phones/tablets will stop working. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.rt_token.setText(generate_token())
        self._refresh_rt_url()

    def _refresh_rt_url(self) -> None:
        """Rebuild the bookmark URL display and QR code when port or token changes."""
        from .trigger_server import detect_lan_ip
        host = detect_lan_ip()
        port = self.rt_port.value()
        token = self.rt_token.text().strip()
        if not token:
            self.rt_url_display.setText("")
            self.rt_qr_label.clear()
            self.rt_qr_label.setFixedHeight(0)
            return
        url = f"http://{host}:{port}/?t={token}"
        self.rt_url_display.setText(url)
        self._render_qr(url)

    def _render_qr(self, url: str) -> None:
        """Render the URL as a QR code into rt_qr_label. Silently no-op if
        the qrcode library isn't installed (we show the URL text instead)."""
        try:
            import qrcode
            from io import BytesIO
            qr = qrcode.QRCode(box_size=6, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            qimg = QImage.fromData(buf.getvalue(), "PNG")
            pix = QPixmap.fromImage(qimg)
            self.rt_qr_label.setPixmap(pix)
            self.rt_qr_label.setFixedHeight(pix.height() + 16)
        except ImportError:
            self.rt_qr_label.clear()
            self.rt_qr_label.setText(
                "(Install 'qrcode' to see a scannable QR here.)"
            )
            self.rt_qr_label.setStyleSheet(
                "color: #A9B1BD; font-size: 11px; padding: 8px;"
            )
        except Exception as e:
            self.rt_qr_label.clear()
            self.rt_qr_label.setText(f"(QR render failed: {e})")

    def _copy_url(self) -> None:
        from PySide6.QtWidgets import QApplication
        text = self.rt_url_display.text().strip()
        if text:
            QApplication.clipboard().setText(text)

    def _update_rt_help(self) -> None:
        """Keep the help text accurate — warn about Telegram requirement."""
        tg_ok = bool(self.token_input.text().strip() and self.chat_input.text().strip())
        tg_mode_on = self.mode_combo.currentData() == "telegram"
        if tg_ok:
            msg = (
                "Scan the QR code with your phone, or copy the URL to any device. "
                "Open it in a browser and tap 'Ask Screen' or 'Quiz Mode'. "
                "Answers are sent to Telegram so the host screen stays silent. "
                "The URL is a shared secret — anyone with it can trigger captures. "
                "Click Regenerate to invalidate old bookmarks."
            )
            if not tg_mode_on:
                msg += (
                    "\n\nNote: your default output mode is 'screen', but remote "
                    "triggers always route to Telegram regardless."
                )
            color = "#A9B1BD"
        else:
            msg = (
                "⚠ Remote trigger requires Telegram to be configured (bot token + "
                "chat ID above). Set those up first, otherwise triggers will fail "
                "with 'Telegram not configured'."
            )
            color = "#E85C5C"
        self.rt_help.setText(msg)
        self.rt_help.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _accept(self) -> None:
        # Validate keyboard hotkeys are non-empty
        ask = self.ask_edit.text().strip()
        quiz = self.quiz_edit.text().strip()
        region = self.region_edit.text().strip()
        region_quiz = self.region_quiz_edit.text().strip()
        if not (ask and quiz and region and region_quiz):
            QMessageBox.warning(self, "Missing hotkey",
                                "All four keyboard hotkeys must be set.")
            return
        if len({ask, quiz, region, region_quiz}) != 4:
            QMessageBox.warning(self, "Duplicate hotkeys",
                                "Each action needs a different keyboard hotkey.")
            return

        # Mouse hotkeys are optional. Collect, validate, and warn as needed.
        ask_m = self.ask_mouse_edit.text().strip()
        quiz_m = self.quiz_mouse_edit.text().strip()
        region_m = self.region_mouse_edit.text().strip()
        region_quiz_m = self.region_quiz_mouse_edit.text().strip()
        mouse_values = [v for v in (ask_m, quiz_m, region_m, region_quiz_m) if v]
        if len(mouse_values) != len(set(mouse_values)):
            QMessageBox.warning(
                self, "Duplicate mouse hotkeys",
                "Each mouse hotkey must be unique (leave blank to disable).",
            )
            return

        # Warn if any mouse hotkey is bare "middle_click" (would break autoscroll
        # system-wide). Only ask once per OK press.
        plain_middle_bindings = [
            label for label, v in [
                ("Ask about screen", ask_m),
                ("Quiz mode (full screen)", quiz_m),
                ("Ask about a region", region_m),
                ("Quiz mode on a region", region_quiz_m),
            ] if v == "middle_click"
        ]
        if plain_middle_bindings:
            affected = ", ".join(plain_middle_bindings)
            resp = QMessageBox.warning(
                self, "Middle-click will break autoscroll",
                "You're binding plain middle-click (no modifier) to "
                f"{affected}.\n\n"
                "This will trigger ScreenSense every time you press the mouse "
                "wheel system-wide, which breaks middle-click autoscroll in "
                "browsers and other apps that use it.\n\n"
                "Safer alternatives: shift+middle_click, ctrl+middle_click, "
                "or a side mouse button (mouse_button_4 / mouse_button_5).\n\n"
                "Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
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
        self.config.hotkey_mouse = ask_m
        self.config.quiz_hotkey_mouse = quiz_m
        self.config.region_hotkey_mouse = region_m
        self.config.region_quiz_hotkey_mouse = region_quiz_m
        self.config.output_mode = mode
        self.config.telegram_token = self.token_input.text().strip()
        self.config.telegram_chat_id = self.chat_input.text().strip()
        # Remote trigger server
        self.config.trigger_server_enabled = self.rt_enable.isChecked()
        self.config.trigger_server_port = self.rt_port.value()
        self.config.trigger_server_token = self.rt_token.text().strip()
        self.config.save()
        self.accept()
