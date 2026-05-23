"""Snipping-tool-style region selector.

Renders a fullscreen semi-transparent overlay. User clicks and drags to draw
a rectangle. On release, the chosen region is emitted as (x, y, w, h) in
*screen* coordinates (NOT widget-local coordinates — those would be wrong
when there are multiple monitors).

Esc or right-click cancels.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QBrush
from PySide6.QtWidgets import QWidget


class RegionPicker(QWidget):
    """Fullscreen drag-to-select overlay."""

    region_selected = Signal(int, int, int, int)   # (x, y, w, h) in screen coords
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)

        # Cover the entire virtual screen (all monitors)
        primary = QGuiApplication.primaryScreen()
        self._virtual_geom = primary.virtualGeometry()
        self.setGeometry(self._virtual_geom)

        # Qt reports coordinates in *logical* (device-independent) pixels.
        # mss works in *physical* pixels. On a display scaled above 100%
        # (e.g. 125%, 150%, 175%, 200% — very common on modern laptops),
        # these differ by the device pixel ratio. Without this conversion,
        # mss grabs the wrong area of the screen — usually offset and scaled.
        self._dpr = primary.devicePixelRatio()
        print(f"[ScreenSense RegionPicker] virtualGeom={self._virtual_geom}, "
              f"devicePixelRatio={self._dpr}", flush=True)

        self._origin: Optional[QPoint] = None
        self._current: Optional[QPoint] = None

    def showEvent(self, event):
        super().showEvent(event)
        # Steal focus so Esc works immediately
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._cancel()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        self._current = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        self._current = event.position().toPoint()
        rect = QRect(self._origin, self._current).normalized()
        self.hide()

        # Reject anything too small to be useful (accidental clicks).
        if rect.width() < 10 or rect.height() < 10:
            self._cancel()
            return

        # Convert from widget-local (logical) coords to absolute screen
        # coords in PHYSICAL pixels (what mss expects).
        origin = self._virtual_geom.topLeft()
        logical_x = rect.x() + origin.x()
        logical_y = rect.y() + origin.y()
        logical_w = rect.width()
        logical_h = rect.height()
        # Apply the device pixel ratio. Round-to-nearest to avoid losing a
        # pixel column/row at scaling boundaries.
        sx = round(logical_x * self._dpr)
        sy = round(logical_y * self._dpr)
        sw = round(logical_w * self._dpr)
        sh = round(logical_h * self._dpr)
        print(f"[ScreenSense RegionPicker] logical=({logical_x},{logical_y} "
              f"{logical_w}x{logical_h}) dpr={self._dpr} -> "
              f"physical=({sx},{sy} {sw}x{sh})", flush=True)

        # CRITICAL: do not emit or call deleteLater() synchronously inside the
        # event handler. Both can interact badly with the in-flight event
        # delivery — particularly when the receiver opens new windows or
        # starts threads. Defer to the next event-loop iteration so this
        # handler returns cleanly first.
        #
        # The 50ms delay also gives Windows GDI a moment to finish compositing
        # after we hide() — mss screen-grabs that come back-to-back with the
        # overlay being hidden have caused hard crashes in testing.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, lambda: self._finalize(sx, sy, sw, sh))

    def _finalize(self, x: int, y: int, w: int, h: int) -> None:
        """Run after mouseReleaseEvent has returned. Emit the region and clean up."""
        self.region_selected.emit(x, y, w, h)
        self.deleteLater()

    def _cancel(self) -> None:
        self.hide()
        # Defer for the same reasons as _finalize above.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self._finalize_cancel)

    def _finalize_cancel(self) -> None:
        self.cancelled.emit()
        self.deleteLater()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dim everything
        dim = QColor(0, 0, 0, 110)
        p.fillRect(self.rect(), dim)

        # Hint text in the top-center when nothing is selected yet
        if self._origin is None:
            font = QFont("Segoe UI", 16, QFont.Weight.Bold)
            p.setFont(font)
            fm = p.fontMetrics()
            hint = "Click and drag to select a region   ·   Esc to cancel"
            tw = fm.horizontalAdvance(hint)
            th = fm.height()
            pad_x, pad_y = 24, 14
            bx = (self.width() - tw) / 2 - pad_x
            by = 40
            bw = tw + pad_x * 2
            bh = th + pad_y * 2
            # Pill background
            p.setPen(QPen(QColor(255, 230, 0), 2))
            p.setBrush(QBrush(QColor(15, 15, 20, 230)))
            p.drawRoundedRect(int(bx), int(by), int(bw), int(bh), 14, 14)
            p.setPen(QColor(255, 255, 255))
            p.drawText(int(bx + pad_x), int(by + pad_y + fm.ascent()), hint)
            return

        # Draw selection rectangle with a "hole" through the dim layer
        rect = QRect(self._origin, self._current).normalized()
        # Erase the dim inside the selection
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(rect, Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # Outline
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 220), 4))
        p.drawRect(rect)
        p.setPen(QPen(QColor(255, 230, 0), 2))
        p.drawRect(rect)

        # Size label in the corner of the selection
        size_text = f"{rect.width()} × {rect.height()}"
        font = QFont("Segoe UI", 12, QFont.Weight.Bold)
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(size_text)
        th = fm.height()
        pad = 6
        label_w = tw + pad * 2
        label_h = th + pad * 2

        # Position: bottom-right of selection, falling outside if there's no room inside
        lx = rect.right() - label_w
        ly = rect.bottom() + 6
        # If that puts us off-screen at the bottom, move above instead
        if ly + label_h > self.height():
            ly = rect.top() - label_h - 6
        # Clamp horizontally
        if lx < 0:
            lx = 0
        if lx + label_w > self.width():
            lx = self.width() - label_w

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(15, 15, 20, 240)))
        p.drawRoundedRect(lx, ly, label_w, label_h, 6, 6)
        p.setPen(QColor(255, 230, 0))
        p.drawText(lx + pad, ly + pad + fm.ascent(), size_text)
