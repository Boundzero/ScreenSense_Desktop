"""Main application controller — wires tray, hotkey, watcher, API, and UI."""
from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap, QPainter, QFont
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon

from pynput import keyboard

from .config import Config
from .highlight import HighlightOverlay, HighlightTarget
from .llm_client import Conversation, ask_about_screen, ask_followup, make_client
from .providers import PROVIDERS
from .quiz import QuizResult, run_quiz_analysis
from .region_picker import RegionPicker
from .setup_dialog import obtain_provider_and_key, add_provider_dialog
from .ui import AnswerPanel, Toast
from .watcher import Frame, ScreenWatcher, start_watcher


def _make_tray_icon() -> QIcon:
    """Generate a simple monogram icon at runtime — no asset files to ship."""
    pm = QPixmap(64, 64)
    pm.fill(QColor("#4F6BED"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor("white"))
    f = QFont("Segoe UI", 28, QFont.Weight.Bold)
    p.setFont(f)
    p.drawText(pm.rect(), 0x84, "S")  # Qt.AlignCenter == 0x84
    p.end()
    return QIcon(pm)


class HotkeyListener(QObject):
    """Bridges pynput's background thread to Qt's signal/slot via signals."""
    ask_triggered = Signal()
    quiz_triggered = Signal()
    region_triggered = Signal()
    region_quiz_triggered = Signal()

    def __init__(self, ask_hotkey: str, quiz_hotkey: str, region_hotkey: str,
                 region_quiz_hotkey: str):
        super().__init__()
        self._listener: Optional[keyboard.GlobalHotKeys] = None
        self._bindings = {
            self._translate(ask_hotkey): self.ask_triggered.emit,
            self._translate(quiz_hotkey): self.quiz_triggered.emit,
            self._translate(region_hotkey): self.region_triggered.emit,
            self._translate(region_quiz_hotkey): self.region_quiz_triggered.emit,
        }

    @staticmethod
    def _translate(hotkey: str) -> str:
        """Convert friendly hotkey syntax to pynput's required format.

        pynput's parser requires:
          - Single characters: no brackets — e.g. 'a', 'c', '/'
          - Multi-character key names: wrapped in angle brackets — e.g. '<ctrl>',
            '<shift>', '<space>', '<f5>', '<enter>'

        Our friendly format accepts either style — users can write
        'ctrl+shift+space' or '<ctrl>+<shift>+<space>' and we normalize.
        """
        out_parts: list[str] = []
        for p in hotkey.lower().split("+"):
            p = p.strip()
            if not p:
                continue
            # Strip any brackets the user may have included so we can normalize.
            if p.startswith("<") and p.endswith(">"):
                p = p[1:-1]
            # Single-char keys: pass through as-is. Anything else needs brackets.
            if len(p) == 1:
                out_parts.append(p)
            else:
                out_parts.append(f"<{p}>")
        return "+".join(out_parts)

    def start(self) -> None:
        self._listener = keyboard.GlobalHotKeys(self._bindings)
        self._listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            # Wait briefly for the listener thread to actually finish and
            # release its OS-level keyboard hook. Without this, immediately
            # creating a new GlobalHotKeys (as rebind does) can race with the
            # old hook still being torn down, leaving the new hotkeys dead.
            try:
                self._listener.join(timeout=1.0)
            except RuntimeError:
                pass  # thread may not have fully started; safe to ignore
            self._listener = None

    def rebind(self, ask_hotkey: str, quiz_hotkey: str, region_hotkey: str,
               region_quiz_hotkey: str) -> None:
        """Stop the current listener and start a new one with new bindings.

        pynput's GlobalHotKeys bindings are fixed at construction, so changing
        hotkeys means tearing down and recreating the listener. A listener is
        a threading.Thread and cannot be restarted once stopped — we always
        create a fresh GlobalHotKeys instance here.
        """
        self.stop()
        self._bindings = {
            self._translate(ask_hotkey): self.ask_triggered.emit,
            self._translate(quiz_hotkey): self.quiz_triggered.emit,
            self._translate(region_hotkey): self.region_triggered.emit,
            self._translate(region_quiz_hotkey): self.region_quiz_triggered.emit,
        }
        self.start()


class ScreenSenseApp(QObject):
    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.config = Config.load()

        result = obtain_provider_and_key(self.config)
        if not result:
            QMessageBox.information(None, "ScreenSense", "No API key provided. Exiting.")
            sys.exit(0)
        provider_id, api_key, model, extras = result

        try:
            self.provider = make_client(provider_id, api_key, model, **extras)
        except ImportError as e:
            QMessageBox.critical(
                None, "Missing SDK",
                f"The Python SDK for {provider_id} isn't installed.\n\n"
                f"Run:  pip install {self._sdk_package(provider_id)}\n\n"
                f"Details: {e}",
            )
            sys.exit(1)
        except Exception as e:
            QMessageBox.critical(
                None, "Provider init failed",
                f"Couldn't initialize {provider_id}: {type(e).__name__}: {e}",
            )
            sys.exit(1)

        self.conversation = Conversation()
        self.latest_frame: Optional[Frame] = None
        self.active_toast: Optional[Toast] = None

        # UI surfaces
        self.panel = AnswerPanel()
        self.panel.ai_name = self.provider.display_name
        self.panel.ask_followup.connect(self._handle_followup)
        self.panel.capture_new.connect(self._capture_and_ask)
        self.panel.closed.connect(self._reset_conversation)

        # Tray
        self.tray = QSystemTrayIcon(_make_tray_icon())
        self._update_tray_tooltip()
        menu = QMenu()
        self.act_ask = QAction("Ask about screen now")
        self.act_ask.triggered.connect(self._capture_and_ask)
        self.act_quiz = QAction("Find correct answer  (Quiz mode)")
        self.act_quiz.triggered.connect(self._capture_and_quiz)
        self.act_region_ask = QAction("Ask about a region…")
        self.act_region_ask.triggered.connect(self._capture_region_and_ask)
        self.act_region_quiz = QAction("Quiz mode on a region…")
        self.act_region_quiz.triggered.connect(self._capture_region_and_quiz)
        self.act_toggle = QAction("Auto-suggest on errors", checkable=True)
        self.act_toggle.setChecked(self.config.auto_suggest)
        self.act_toggle.toggled.connect(self._toggle_auto)
        self.act_settings = QAction("Settings…")
        self.act_settings.triggered.connect(self._open_settings)
        self.act_quit = QAction("Quit")
        self.act_quit.triggered.connect(self._quit)

        # Provider submenu — built dynamically based on what's configured.
        self.provider_menu = QMenu("AI Provider")
        self._rebuild_provider_menu()

        menu.addAction(self.act_ask)
        menu.addAction(self.act_quiz)
        menu.addSeparator()
        menu.addAction(self.act_region_ask)
        menu.addAction(self.act_region_quiz)
        menu.addSeparator()
        menu.addMenu(self.provider_menu)
        menu.addAction(self.act_toggle)
        menu.addAction(self.act_settings)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_click)
        self.tray.show()

        # Watcher thread
        self.watcher, self.watcher_thread = start_watcher(fps=self.config.poll_fps)
        self.watcher.significant_change.connect(self._on_frame)
        self.watcher.interesting_detected.connect(self._on_interesting)
        self.watcher_thread.start()

        # Global hotkeys
        self.hotkey = HotkeyListener(
            self.config.hotkey,
            self.config.quiz_hotkey,
            self.config.region_hotkey,
            self.config.region_quiz_hotkey,
        )
        self.hotkey.ask_triggered.connect(self._capture_and_ask)
        self.hotkey.quiz_triggered.connect(self._capture_and_quiz)
        self.hotkey.region_triggered.connect(self._capture_region_and_ask)
        self.hotkey.region_quiz_triggered.connect(self._capture_region_and_quiz)
        self.hotkey.start()

        # Optional mouse hotkeys (parallel trigger path — each action can
        # ALSO fire from a mouse chord in addition to its keyboard hotkey).
        # The mouse listener's callbacks route into Qt via QMetaObject so
        # thread safety matches the keyboard side. The queued-signal shape
        # of the triggered slots means we can just call Signal.emit from
        # the pynput mouse thread and Qt marshals it.
        from .mouse_hotkeys import MouseHotkeyListener
        self.mouse_hotkey = MouseHotkeyListener()
        self._refresh_mouse_bindings()
        self.mouse_hotkey.start()

        # Worker references (Qt requires keeping them alive)
        self._worker = None
        self._worker_thread = None
        self._quiz_worker = None
        self._quiz_thread = None
        self._active_highlight: Optional[HighlightOverlay] = None
        self._active_picker: Optional[RegionPicker] = None

        # Per-request Telegram override. Set to True by remote-trigger
        # requests just before emitting an action signal, so the output
        # path routes to Telegram regardless of global output_mode. Cleared
        # by the finish/fail handlers.
        self._force_telegram: bool = False

        # Optional remote trigger server (opt-in via Settings).
        self.trigger_server = None
        self._maybe_start_trigger_server()

    def _maybe_start_trigger_server(self) -> None:
        """Start the HTTP trigger server if enabled in config.

        Called on startup and whenever settings change. Safe to call when
        server is already running — stops the old instance first.
        """
        # Stop any existing server before (re)evaluating config.
        if self.trigger_server is not None:
            try:
                self.trigger_server.stop()
            except Exception:
                pass
            self.trigger_server = None

        if not self.config.trigger_server_enabled:
            return
        if not self.config.trigger_server_token:
            # Enabled but no token saved — generate one and persist.
            from .trigger_server import generate_token
            self.config.trigger_server_token = generate_token()
            self.config.save()

        from .trigger_server import TriggerServer
        try:
            self.trigger_server = TriggerServer(
                port=int(self.config.trigger_server_port or 7474),
                token=self.config.trigger_server_token,
                on_trigger=self._handle_remote_trigger,
            )
            self.trigger_server.start()
            import sys
            print(
                f"[ScreenSense] Remote trigger server listening on port "
                f"{self.config.trigger_server_port}",
                file=sys.stderr, flush=True,
            )
        except Exception as e:
            import sys
            print(f"[ScreenSense] Failed to start trigger server: {e}",
                  file=sys.stderr, flush=True)
            self.trigger_server = None

    def _handle_remote_trigger(self, action: str) -> tuple[bool, str]:
        """Called by the HTTP server when a web-page button is tapped.

        Runs on the HTTP worker thread. We set the force-telegram flag
        and emit the matching signal; signal delivery is QueuedConnection
        so the actual capture + LLM call happens on the Qt main thread.

        Returns (ok, error_message). The web page displays the error to
        the user if ok=False.
        """
        if not (self.config.telegram_token and self.config.telegram_chat_id):
            return (False, "Telegram is not configured on the host. "
                           "Set it up in Settings → Output.")
        self._force_telegram = True
        if action == "ask":
            self.hotkey.ask_triggered.emit()
        elif action == "quiz":
            self.hotkey.quiz_triggered.emit()
        else:
            self._force_telegram = False
            return (False, f"Unknown action: {action}")
        return (True, "")

    # --- frame & event handlers ---

    @staticmethod
    def _sdk_package(provider_id: str) -> str:
        return {
            "anthropic": "anthropic",
            "openai": "openai",
            "google": "google-genai",
            "openwebui": "openai",   # uses the OpenAI SDK with a different base_url
        }.get(provider_id, provider_id)

    def _update_tray_tooltip(self) -> None:
        active = PROVIDERS.get(self.config.provider, (None, None, None))
        active_name = self.provider.display_name if hasattr(self, "provider") else self.config.provider
        self.tray.setToolTip(
            f"ScreenSense — using {active_name}\n"
            f"{self.config.hotkey} — ask about screen\n"
            f"{self.config.quiz_hotkey} — find correct answer\n"
            f"{self.config.region_hotkey} — ask about a region\n"
            f"{self.config.region_quiz_hotkey} — quiz mode on a region"
        )

    def _rebuild_provider_menu(self) -> None:
        """Build the AI Provider submenu reflecting what's configured."""
        self.provider_menu.clear()
        configured = self.config.configured_providers()
        active = self.config.provider

        for pid, (cls, _models, _default) in PROVIDERS.items():
            label = cls.display_name
            if pid not in configured:
                label += "   (not configured)"
            elif pid == active:
                label = "✓  " + label + "   (active)"
            else:
                label = "    " + label
            act = QAction(label, self.provider_menu)
            act.setData(pid)
            act.triggered.connect(lambda checked=False, p=pid: self._switch_provider(p))
            self.provider_menu.addAction(act)

        self.provider_menu.addSeparator()
        add_act = QAction("Add / configure another provider…", self.provider_menu)
        add_act.triggered.connect(self._add_provider)
        self.provider_menu.addAction(add_act)

    def _switch_provider(self, provider_id: str) -> None:
        if provider_id == self.config.provider:
            return
        if not self.config.has_key_for(provider_id):
            QMessageBox.information(
                None, "Not configured",
                f"You haven't added an API key for "
                f"{PROVIDERS[provider_id][0].display_name} yet. Use "
                f"'Add / configure another provider…' first.",
            )
            return
        # Resolve the key — easy if plaintext, prompts if encrypted.
        if provider_id in self.config.keys_plaintext:
            api_key = self.config.keys_plaintext[provider_id]
        else:
            from .setup_dialog import UnlockDialog
            from .config import decrypt_api_key
            while True:
                dlg = UnlockDialog()
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    return
                try:
                    api_key = decrypt_api_key(
                        self.config.keys_encrypted[provider_id],
                        self.config.salt_b64, dlg.passphrase,
                    )
                    break
                except ValueError:
                    QMessageBox.warning(None, "Wrong passphrase", "Try again.")

        model = self.config.model_for(provider_id) or PROVIDERS[provider_id][2]
        extras = self.config.extras.get(provider_id, {})
        try:
            new_provider = make_client(provider_id, api_key, model, **extras)
        except ImportError:
            QMessageBox.critical(
                None, "Missing SDK",
                f"The SDK for {PROVIDERS[provider_id][0].display_name} isn't "
                f"installed.\n\nRun:  pip install "
                f"{self._sdk_package(provider_id)}",
            )
            return
        except Exception as e:
            QMessageBox.critical(
                None, "Switch failed",
                f"Couldn't switch: {type(e).__name__}: {e}",
            )
            return

        self.provider = new_provider
        self.config.provider = provider_id
        self.config.save()
        self.conversation = Conversation()   # reset; histories aren't portable across providers
        self.panel.ai_name = self.provider.display_name
        self._rebuild_provider_menu()
        self._update_tray_tooltip()
        self.tray.showMessage(
            "ScreenSense",
            f"Now using {PROVIDERS[provider_id][0].display_name} ({model})",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

    def _add_provider(self) -> None:
        if add_provider_dialog(self.config, existing_passphrase=None):
            self._rebuild_provider_menu()
            QMessageBox.information(
                None, "Added",
                "Provider configured. Use the AI Provider menu to switch to it.",
            )


    def _on_frame(self, frame: Frame) -> None:
        self.latest_frame = frame

    def _on_interesting(self, frame: Frame) -> None:
        if not self.config.auto_suggest:
            return
        # In Telegram mode, the whole point is that nothing appears on screen.
        # The auto-suggest toast is an on-screen prompt, and auto-firing to
        # Telegram on every detected error would spam the chat. So we suppress
        # auto-suggest entirely in Telegram mode — the user drives via hotkeys.
        if self._telegram_mode():
            return
        if self.panel.isVisible():
            return  # don't pester when user is already in a session
        hits = ", ".join(frame.interesting_hits[:2])
        toast = Toast(f"I see something that looks like an error:\n{hits}", "Analyze")
        toast.clicked.connect(lambda: self._toast_clicked(toast, frame))
        self.active_toast = toast
        toast.show()

    def _toast_clicked(self, toast: Toast, frame: Frame) -> None:
        toast.fade_out()
        self._ask_about(frame, question="There's likely an error on this screen. Identify it and explain how to fix it.")

    def _telegram_mode(self) -> bool:
        """True if answers should go to Telegram instead of the screen.

        Honors a per-request override (`_force_telegram`) so remote-trigger
        requests always route to Telegram regardless of global output_mode.
        The override is set just before emitting a trigger signal and cleared
        when the request finishes (see _on_finished / _on_quiz_finished).
        """
        if getattr(self, "_force_telegram", False):
            return bool(self.config.telegram_token) and bool(self.config.telegram_chat_id)
        return (
            self.config.output_mode == "telegram"
            and bool(self.config.telegram_token)
            and bool(self.config.telegram_chat_id)
        )

    def _send_telegram(self, text: str) -> None:
        """Send text to Telegram. On failure, fall back to an on-screen error
        (Telegram-mode errors must never be silent)."""
        from .telegram import send_to_telegram
        try:
            send_to_telegram(
                self.config.telegram_token,
                self.config.telegram_chat_id,
                text,
            )
        except Exception as e:
            # Surface the failure on screen so a misconfig isn't invisible.
            self.panel.show_error(
                f"Couldn't send to Telegram: {e}\n\n"
                f"The answer was:\n\n{text}"
            )

    def _telegram_notice(self, text: str) -> None:
        """Best-effort start/status notice to Telegram. Swallows errors so a
        notice failure doesn't interrupt the actual request — the real answer
        send later will surface any persistent problem."""
        from .telegram import send_to_telegram
        try:
            send_to_telegram(
                self.config.telegram_token,
                self.config.telegram_chat_id,
                text,
            )
        except Exception:
            pass

    def _toggle_auto(self, enabled: bool) -> None:
        self.config.auto_suggest = enabled
        self.config.save()

    def _open_settings(self) -> None:
        """Open the settings dialog; apply hotkey + output changes on accept."""
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.config)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        # Hotkeys may have changed — rebind both listeners.
        self.hotkey.rebind(
            self.config.hotkey,
            self.config.quiz_hotkey,
            self.config.region_hotkey,
            self.config.region_quiz_hotkey,
        )
        self._refresh_mouse_bindings()
        # Trigger server settings may have changed — restart if needed.
        self._maybe_start_trigger_server()
        self._update_tray_tooltip()

    def _refresh_mouse_bindings(self) -> None:
        """(Re)apply the mouse hotkey bindings from current config.

        Each callback is the matching HotkeyListener signal's emit method.
        The signals are declared with QueuedConnection on the receiving slot
        (see initial connect calls above), so emitting from the pynput mouse
        thread is safe — Qt marshals the call back to the main thread.
        """
        chords: dict = {}
        for hk, sig in (
            (self.config.hotkey_mouse,          self.hotkey.ask_triggered),
            (self.config.quiz_hotkey_mouse,     self.hotkey.quiz_triggered),
            (self.config.region_hotkey_mouse,   self.hotkey.region_triggered),
            (self.config.region_quiz_hotkey_mouse, self.hotkey.region_quiz_triggered),
        ):
            if hk:
                chords[hk] = sig.emit
        self.mouse_hotkey.set_bindings(chords)

    def _on_tray_click(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._capture_and_ask()

    # --- API request flow ---

    def _get_capture_frame(self) -> Optional[Frame]:
        """Get a frame for capture-based actions.

        Prefers the most recent frame from the watcher (already perceptually
        hashed). If the watcher hasn't produced one yet (first 1-2 seconds
        of app startup, or watcher failing), fall back to a synchronous grab.
        """
        if self.latest_frame is not None:
            return self.latest_frame
        return self.watcher.grab_now()

    def _capture_and_ask(self) -> None:
        frame = self._get_capture_frame()
        if frame is None:
            QMessageBox.warning(
                None,
                "ScreenSense — capture failed",
                "Couldn't capture the screen. This usually means screen capture "
                "is blocked (DRM-protected window in focus, or remote desktop "
                "without screen capture permission). Try clicking on the desktop "
                "first, then retry.",
            )
            return
        self._reset_conversation()
        self._ask_about(frame)

    def _capture_and_quiz(self) -> None:
        """Find and highlight the correct answer to a question on screen."""
        frame = self._get_capture_frame()
        if frame is None:
            QMessageBox.warning(
                None,
                "ScreenSense — capture failed",
                "Couldn't capture the screen. This usually means screen capture "
                "is blocked (DRM-protected window in focus, or remote desktop "
                "without screen capture permission). Try clicking on the desktop "
                "first, then retry.",
            )
            return

        # Dismiss any existing highlight before starting a new one
        self._dismiss_highlight()

        if self._telegram_mode():
            self._telegram_notice("📸 ScreenSense captured your screen — finding the answer…")
        else:
            self.panel.show_thinking("Finding the correct answer…")

        # OCR happens inside the worker thread now — this avoids cross-thread
        # access of the onnxruntime session that would crash the app on Windows.
        self._last_quiz_image = frame.image
        worker, thread = run_quiz_analysis(self.provider, frame.image)
        # Use QueuedConnection explicitly: signals emitted from a worker thread
        # must be queued back to the main thread, otherwise the slot runs on
        # the worker thread and Qt widget access crashes.
        from PySide6.QtCore import Qt
        worker.finished.connect(self._on_quiz_finished, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._quiz_worker, self._quiz_thread = worker, thread
        thread.start()

    def _capture_region_and_ask(self) -> None:
        """Show region picker, then ask Claude about just that region."""
        self._show_region_picker(self._on_region_for_ask)

    def _capture_region_and_quiz(self) -> None:
        """Show region picker, then run quiz mode on just that region."""
        self._show_region_picker(self._on_region_for_quiz)

    def _show_region_picker(self, on_selected) -> None:
        picker = RegionPicker()
        picker.region_selected.connect(on_selected)
        picker.cancelled.connect(lambda: None)
        self._active_picker = picker   # keep a reference so it doesn't get GC'd
        picker.show()
        picker.raise_()
        picker.activateWindow()

    def _on_region_for_ask(self, x: int, y: int, w: int, h: int) -> None:
        cropped = self._grab_region(x, y, w, h)
        if cropped is None:
            return
        self._reset_conversation()
        self._ask_about(cropped)

    def _on_region_for_quiz(self, x: int, y: int, w: int, h: int) -> None:
        cropped = self._grab_region(x, y, w, h)
        if cropped is None:
            return
        self._dismiss_highlight()
        if self._telegram_mode():
            self._telegram_notice("📸 ScreenSense captured a region — finding the answer…")
        else:
            self.panel.show_thinking("Finding the correct answer…")
        # OCR happens inside the worker thread — see _capture_and_quiz comment.
        # OCR boxes will be in the cropped image's coordinate space; we shift
        # the final highlight rects back to screen coords using the offset
        # we stash on self below.
        self._pending_region_offset = (x, y)
        self._last_quiz_image = cropped.image
        worker, thread = run_quiz_analysis(self.provider, cropped.image)
        # CRITICAL: use a bound method (not a lambda) so Qt knows the slot
        # belongs to self (a QObject in the main thread) and queues delivery.
        # Lambdas defined inline have no QObject parent and end up running
        # on the emitting thread (the worker), which crashes hard when the
        # slot touches Qt widgets.
        from PySide6.QtCore import Qt
        worker.finished.connect(self._on_region_quiz_finished, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._quiz_worker, self._quiz_thread = worker, thread
        thread.start()

    def _on_region_quiz_finished(self, result) -> None:
        """Bound-method slot for region-quiz results. Runs on the main thread."""
        off_x, off_y = getattr(self, "_pending_region_offset", (0, 0))
        for ans in result.answers:
            if ans.highlight_rect is not None:
                x, y, w, h = ans.highlight_rect
                ans.highlight_rect = (x + off_x, y + off_y, w, h)
        self._on_quiz_finished(result)

    def _on_quiz_finished_with_offset(self, result, off_x: int, off_y: int) -> None:
        """Legacy entry point — preserved in case anything else calls it."""
        for ans in result.answers:
            if ans.highlight_rect is not None:
                x, y, w, h = ans.highlight_rect
                ans.highlight_rect = (x + off_x, y + off_y, w, h)
        self._on_quiz_finished(result)

    def _grab_region(self, x: int, y: int, w: int, h: int) -> Optional["Frame"]:
        """Capture just the specified screen region."""
        from .watcher import Frame
        import time as _time
        try:
            import mss
            from PIL import Image
            # Sanity-check the coordinates — mss can crash hard if the region
            # extends beyond the virtual screen or has zero/negative dimensions.
            if w <= 0 or h <= 0:
                print(f"[ScreenSense] _grab_region: invalid size w={w} h={h}",
                      file=sys.stderr, flush=True)
                return None
            print(f"[ScreenSense] _grab_region: grabbing x={x} y={y} w={w} h={h}",
                  file=sys.stderr, flush=True)
            with mss.mss() as sct:
                # Clip to the virtual screen so we don't pass out-of-bounds
                # coords to GDI. mss.monitors[0] is the union of all monitors.
                vs = sct.monitors[0]
                left = max(x, vs["left"])
                top = max(y, vs["top"])
                right = min(x + w, vs["left"] + vs["width"])
                bottom = min(y + h, vs["top"] + vs["height"])
                if right <= left or bottom <= top:
                    print(f"[ScreenSense] region fully off-screen, skipping",
                          file=sys.stderr, flush=True)
                    return None
                clipped = {
                    "left": left,
                    "top": top,
                    "width": right - left,
                    "height": bottom - top,
                }
                raw = sct.grab(clipped)
                img = Image.frombytes("RGB", raw.size, raw.rgb)
            return Frame(image=img, timestamp=_time.time())
        except Exception as e:
            import traceback
            print(f"[ScreenSense] _grab_region failed:\n"
                  f"{traceback.format_exc()}", file=sys.stderr, flush=True)
            QMessageBox.warning(
                None,
                "ScreenSense — region capture failed",
                f"Couldn't capture that region: {type(e).__name__}: {e}",
            )
            return None

    def _on_quiz_finished(self, result: QuizResult) -> None:
        try:
            self._on_quiz_finished_impl(result)
        finally:
            # Clear per-request Telegram override (set by remote-trigger
            # requests) no matter how the handler exited.
            self._force_telegram = False

    def _on_quiz_finished_impl(self, result: QuizResult) -> None:
        if not result.found_question or not result.answers:
            # The model didn't see a question.
            if self._telegram_mode():
                # In Telegram mode, just report it to Telegram and stop —
                # the seed-a-conversation flow is an on-screen interaction.
                self._send_telegram(
                    "ScreenSense: no multiple-choice question found in that capture."
                )
                return
            # On-screen: seed the conversation with the same image so they can
            # just ask a follow-up question in plain English.
            img = getattr(self, "_last_quiz_image", None)
            self.panel.body.clear()
            self.panel.title.setText("ScreenSense")
            if img is not None:
                from .llm_client import Turn
                from .watcher import to_png_bytes
                self._reset_conversation()
                self.conversation.turns.append(Turn(
                    role="user",
                    text="I'm going to ask you about this screenshot. Wait for my question.",
                    image_png=to_png_bytes(img),
                ))
                self.conversation.turns.append(Turn(
                    role="assistant",
                    text="Got it — what would you like to know about this screenshot?",
                ))
                self.panel.body.setPlainText(
                    "I couldn't confidently identify a multiple-choice question "
                    "in that capture. But I can see the screenshot — just type "
                    "what you'd like to know in the box below.\n\n"
                    "(For example: 'What is the correct answer?' or "
                    "'Explain what's shown here.')"
                )
            else:
                self.panel.body.setPlainText(
                    "No multiple-choice question found on screen.\n\n"
                    "Quiz mode looks for questions with answer choices like A/B/C/D "
                    "or 1/2/3/4. Try the regular hotkey to ask anything about the screen."
                )
            self.panel.finish()
            return

        # Build the answer text (used by both screen and Telegram paths).
        answer_text = self._format_quiz_text(result)

        # Telegram mode: send the answer to Telegram, show nothing on screen,
        # and skip the highlight overlay entirely.
        if self._telegram_mode():
            self._send_telegram(answer_text)
            return

        # On-screen mode: show in the panel and build the highlight overlay.
        n = len(result.answers)
        self.panel.body.clear()
        self.panel.title.setText("Quiz answer" if n == 1 else f"Quiz answers ({n})")
        self.panel.body.setPlainText(answer_text)
        self.panel.finish()

        # Build the highlight overlay from whichever answers we could locate
        targets: list[HighlightTarget] = []
        for ans in result.answers:
            if ans.highlight_rect is None:
                continue
            letter_prefix = f"{ans.letter}.  " if ans.letter else ""
            targets.append(HighlightTarget(
                rect=ans.highlight_rect,
                label=f"{letter_prefix}{ans.text}",
            ))

        if not targets:
            return   # nothing to highlight; the panel already explains

        overlay = HighlightOverlay(
            targets=targets,
            question=result.question,
            explanation=result.overall_explanation,
            confidence=result.confidence,
            dim_background=self.config.highlight_dim_background,
            pulse=self.config.highlight_pulse,
            outline_color=QColor(*self.config.highlight_color_rgb),
            outline_width=self.config.highlight_outline_width,
        )
        overlay.dismissed.connect(self._on_highlight_dismissed)
        self._active_highlight = overlay
        overlay.show()
        overlay.raise_()

    def _format_quiz_text(self, result: QuizResult) -> str:
        """Render a QuizResult into the plain-text block shown in the panel
        or sent to Telegram. Identical content for both."""
        n = len(result.answers)
        type_label = {
            "single": "Single answer",
            "select_n": f"Select {n}",
            "select_all": f"Select all that apply  ({n} correct)",
        }.get(result.question_type, "Answer")

        lines = [f"Question: {result.question}", "", f"[{type_label}]", ""]
        if n == 1:
            ans = result.answers[0]
            letter = f" ({ans.letter})" if ans.letter else ""
            lines.append(f"Correct answer{letter}: {ans.text}")
            if ans.rationale:
                lines.append(f"Why: {ans.rationale}")
        else:
            lines.append("Correct answers:")
            for i, ans in enumerate(result.answers, start=1):
                letter = f" ({ans.letter})" if ans.letter else ""
                lines.append(f"  {i}.{letter} {ans.text}")
                if ans.rationale:
                    lines.append(f"     → {ans.rationale}")

        if result.overall_explanation:
            lines.append("")
            lines.append(result.overall_explanation)

        lines.append("")
        lines.append(f"Confidence: {result.confidence}")

        # Note on match quality only matters for the on-screen highlight; on
        # Telegram there's no highlight so we skip the "highlighted N of M" note.
        if not self._telegram_mode() and result.matched_count < n:
            missed = n - result.matched_count
            lines.append("")
            if result.matched_count == 0:
                lines.append("Couldn't locate the answer text on screen to highlight. See above.")
            else:
                lines.append(
                    f"Highlighted {result.matched_count} of {n} answers on screen. "
                    f"Couldn't locate {missed} of them — see chat for full text."
                )

        return "\n".join(lines)

    def _dismiss_highlight(self) -> None:
        if self._active_highlight is not None:
            try:
                self._active_highlight._dismiss()
            except RuntimeError:
                pass  # already deleted
            self._active_highlight = None

    def _on_highlight_dismissed(self) -> None:
        self._active_highlight = None

    def _ask_about(self, frame: Frame, question: str = "") -> None:
        if self._telegram_mode():
            self._telegram_notice("📸 ScreenSense captured your screen — analyzing…")
        else:
            self.panel.show_thinking()
        worker, thread = ask_about_screen(self.provider, self.conversation, frame.image, question)
        self._run_worker(worker, thread)

    def _handle_followup(self, question: str) -> None:
        worker, thread = ask_followup(self.provider, self.conversation, question)
        self._run_worker(worker, thread)

    def _run_worker(self, worker, thread) -> None:
        from PySide6.QtCore import Qt
        # Streaming chunks come fast from the worker thread; QueuedConnection
        # marshals each one back to the main thread before it reaches the panel.
        # In Telegram mode we suppress the on-screen panel entirely and instead
        # send the full collected text in _on_finished, so we skip the chunk
        # connection.
        if not self._telegram_mode():
            worker.chunk.connect(self.panel.append_chunk, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._worker, self._worker_thread = worker, thread
        thread.start()

    def _on_finished(self, full_text: str) -> None:
        # Persist the LLM's response in conversation so follow-ups have context.
        from .providers import Turn
        self.conversation.turns.append(Turn(role="assistant", text=full_text))
        if self._telegram_mode():
            self._send_telegram(full_text)
        else:
            self.panel.finish()
        # Clear per-request Telegram override (set by remote-trigger requests).
        self._force_telegram = False

    def _on_failed(self, msg: str) -> None:
        # If this request was triggered remotely (force_telegram) and we have
        # Telegram configured, deliver the error there instead of popping an
        # on-screen panel the user may not be near. Falls through to on-screen
        # if Telegram isn't configured (never silent).
        if getattr(self, "_force_telegram", False) and self._telegram_mode():
            self._telegram_notice(f"ScreenSense error: {msg}")
        else:
            self.panel.show_error(msg)
        self._force_telegram = False

    def _reset_conversation(self) -> None:
        self.conversation = Conversation()

    def _quit(self) -> None:
        self._dismiss_highlight()
        self.hotkey.stop()
        if hasattr(self, "mouse_hotkey"):
            self.mouse_hotkey.stop()
        if getattr(self, "trigger_server", None) is not None:
            try:
                self.trigger_server.stop()
            except Exception:
                pass
        self.watcher.stop()
        self.watcher_thread.quit()
        self.watcher_thread.wait(1500)
        self.tray.hide()
        self.app.quit()
