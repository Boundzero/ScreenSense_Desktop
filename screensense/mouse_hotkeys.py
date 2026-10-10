"""Mouse-button global hotkeys with keyboard-modifier awareness.

pynput provides a high-level `keyboard.GlobalHotKeys` class for keyboard
chords but no equivalent for mouse buttons. We build one here by combining a
`mouse.Listener` (for the button events) with a `keyboard.Listener` (to
track which modifiers are currently held) so chords like
``ctrl+shift+mouse_button_5`` or ``shift+middle_click`` work.

Each registered chord maps to a Qt signal in the main HotkeyListener; this
module only emits callbacks — the Qt wiring stays in app.py.

Hotkey string format (matches the keyboard side):
    - modifiers: "ctrl", "alt", "shift", "cmd"
    - mouse keys: "mouse_button_4" (back), "mouse_button_5" (forward),
                  "middle_click"
    - joined with "+", e.g. "shift+mouse_button_5"

An empty / blank hotkey string means "no binding for this action" and is
silently ignored during registration.
"""
from __future__ import annotations

from typing import Callable, Optional

from pynput import keyboard, mouse


# What a bare mouse-button token looks like in a hotkey string.
MOUSE_BUTTON_TOKENS = {"mouse_button_4", "mouse_button_5", "middle_click"}

# Modifier tokens we recognize (matches the keyboard-side vocabulary).
_MODIFIER_SET = {"ctrl", "alt", "shift", "cmd"}


def is_mouse_hotkey(hotkey: str) -> bool:
    """True if the given hotkey string ends in a mouse button token."""
    if not hotkey:
        return False
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    return bool(parts) and parts[-1] in MOUSE_BUTTON_TOKENS


def _parse(hotkey: str) -> tuple[frozenset[str], str]:
    """Parse 'ctrl+shift+mouse_button_5' into (frozenset{'ctrl','shift'}, 'mouse_button_5').

    Case- and whitespace-insensitive. Only the last token may be the mouse
    button; everything before it must be a modifier.
    """
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    button = parts[-1]
    if button not in MOUSE_BUTTON_TOKENS:
        raise ValueError(f"last token must be a mouse button, got {button!r}")
    mods = parts[:-1]
    for m in mods:
        if m not in _MODIFIER_SET:
            raise ValueError(f"unknown modifier {m!r}")
    return frozenset(mods), button


# Map our mouse-token strings to pynput mouse Button enums. We resolve
# lazily inside the listener because pynput's Button members differ slightly
# between platforms (Button.button8/button9 on X11 vs Button.x1/x2 on
# Windows+macOS). Lookup order favors Windows/mac names first.
def _resolve_button(token: str) -> Optional[mouse.Button]:
    if token == "middle_click":
        return mouse.Button.middle
    # Side buttons: name differs by platform. Try known attributes in order.
    if token == "mouse_button_4":
        for attr in ("x1", "button8", "back"):
            btn = getattr(mouse.Button, attr, None)
            if btn is not None:
                return btn
    elif token == "mouse_button_5":
        for attr in ("x2", "button9", "forward"):
            btn = getattr(mouse.Button, attr, None)
            if btn is not None:
                return btn
    return None


class MouseHotkeyListener:
    """Runs background keyboard + mouse listeners and fires registered
    chord callbacks when mouse-button hotkeys are pressed.

    Call `set_bindings({"ctrl+shift+mouse_button_5": callback, ...})` to
    (re)set the registered chords. Call `start()` once; call `stop()` on
    shutdown. Rebinding does not require a stop/start cycle.
    """

    def __init__(self):
        self._mouse_listener: Optional[mouse.Listener] = None
        self._kb_listener: Optional[keyboard.Listener] = None
        # Currently-held modifier set (ctrl/alt/shift/cmd).
        self._held: set[str] = set()
        # bindings: {pynput.mouse.Button: [(required_mods frozenset, callback), ...]}
        # List because multiple chords can share a button with different mods.
        self._bindings: dict = {}

    # ---------- public API ----------

    def set_bindings(self, chords: dict[str, Callable[[], None]]) -> None:
        """Replace all current bindings.

        chords: {hotkey_string: callback}. Hotkeys that are empty strings
        or that reference an unsupported button on this platform are
        silently skipped (with a stderr note).
        """
        new: dict = {}
        import sys
        for hotkey, cb in chords.items():
            if not hotkey:
                continue
            try:
                mods, token = _parse(hotkey)
            except ValueError as e:
                print(f"[ScreenSense MouseHotkey] skipping {hotkey!r}: {e}",
                      file=sys.stderr, flush=True)
                continue
            btn = _resolve_button(token)
            if btn is None:
                print(f"[ScreenSense MouseHotkey] skipping {hotkey!r}: "
                      f"button {token} not available on this platform",
                      file=sys.stderr, flush=True)
                continue
            new.setdefault(btn, []).append((mods, cb))
        self._bindings = new

    def start(self) -> None:
        if self._kb_listener is None:
            self._kb_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
            )
            self._kb_listener.start()
        if self._mouse_listener is None:
            self._mouse_listener = mouse.Listener(on_click=self._on_click)
            self._mouse_listener.start()

    def stop(self) -> None:
        for lst in (self._mouse_listener, self._kb_listener):
            if lst is not None:
                try:
                    lst.stop()
                except Exception:
                    pass
        self._mouse_listener = None
        self._kb_listener = None
        self._held.clear()

    # ---------- internals ----------

    def _on_key_press(self, key):
        tok = self._mod_token(key)
        if tok:
            self._held.add(tok)

    def _on_key_release(self, key):
        tok = self._mod_token(key)
        if tok:
            self._held.discard(tok)

    @staticmethod
    def _mod_token(key) -> Optional[str]:
        """Map a pynput Key to our modifier token, or None if it's not a
        modifier we care about."""
        K = keyboard.Key
        if key in (K.ctrl, K.ctrl_l, K.ctrl_r):
            return "ctrl"
        if key in (K.alt, K.alt_l, K.alt_r, K.alt_gr):
            return "alt"
        if key in (K.shift, K.shift_l, K.shift_r):
            return "shift"
        if key in (K.cmd, K.cmd_l, K.cmd_r):
            return "cmd"
        return None

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return
        callbacks = self._bindings.get(button)
        if not callbacks:
            return
        current_mods = frozenset(self._held)
        for required, cb in callbacks:
            if required == current_mods:
                try:
                    cb()
                except Exception as e:
                    import sys
                    print(f"[ScreenSense MouseHotkey] callback raised: {e}",
                          file=sys.stderr, flush=True)
                # If multiple chords match the same combo somehow, fire the
                # first and stop so we don't double-trigger.
                return
