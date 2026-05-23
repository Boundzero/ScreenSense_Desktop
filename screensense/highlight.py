"""Accessibility-focused highlight overlay.

Draws thick, high-contrast rectangles over the correct answer(s) on the user's
screen. Supports a single answer (typical MCQ) or multiple answers ("select
all that apply", "select two", etc.).

Design for low-vision users:
  - Bold outline (8-12 px) in a high-contrast color (default: bright yellow on
    a black inner ring, so it's visible on both light and dark backgrounds)
  - Optional screen dimming — everything except the answers goes ~55% darker,
    drawing the eye to the highlights
  - Pulsing animation (toggleable — some users find motion distracting)
  - Numbered badges (1, 2, 3...) on each highlight when there are 2+
  - Combined caption panel listing all correct answers
  - Auto-dismiss after 30 seconds, or Esc, or click anywhere
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import Qt, QPropertyAnimation, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QBrush, QPainterPath
from PySide6.QtWidgets import QPushButton, QWidget


# Accessibility-tuned defaults.
DEFAULT_OUTLINE_COLOR = QColor(255, 230, 0)         # bright yellow
DEFAULT_OUTLINE_WIDTH = 8
CAPTION_FONT_SIZE = 18
DIM_OPACITY = 0.55                                  # how much to darken the rest of the screen


@dataclass
class HighlightTarget:
    """One rectangle to highlight, plus the text it represents."""
    rect: tuple[int, int, int, int]   # (x, y, w, h) in screen coords
    label: str                         # what to show in the caption ("A. Paris")


class HighlightOverlay(QWidget):
    """Transparent fullscreen overlay highlighting one or more rectangles."""

    dismissed = Signal()

    def __init__(
        self,
        targets: list[HighlightTarget],
        question: str = "",
        explanation: str = "",
        confidence: str = "high",
        dim_background: bool = True,
        pulse: bool = True,
        outline_color: QColor = DEFAULT_OUTLINE_COLOR,
        outline_width: int = DEFAULT_OUTLINE_WIDTH,
    ):
        super().__init__()
        if not targets:
            raise ValueError("HighlightOverlay needs at least one target")

        # Convert target rects from PHYSICAL pixels (what mss/OCR emitted) to
        # Qt logical pixels (what we draw in). On a 150%-scaled display this
        # divides every coordinate by 1.5; on a 100% display it's a no-op.
        primary = QGuiApplication.primaryScreen()
        self._dpr = primary.devicePixelRatio()
        if self._dpr != 1.0:
            converted = []
            for t in targets:
                px, py, pw, ph = t.rect
                converted.append(HighlightTarget(
                    rect=(
                        round(px / self._dpr),
                        round(py / self._dpr),
                        round(pw / self._dpr),
                        round(ph / self._dpr),
                    ),
                    label=t.label,
                ))
            self.targets = converted
        else:
            self.targets = targets

        self.question = question
        self.explanation = explanation
        self.confidence = confidence
        self.dim_background = dim_background
        self.outline_color = outline_color
        self.outline_width = outline_width
        self._pulse_phase = 0.0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Cover the full virtual screen (all monitors)
        full = QGuiApplication.primaryScreen().virtualGeometry()
        self.setGeometry(full)

        # Dismiss button
        self.dismiss_btn = QPushButton("Dismiss highlights  (Esc)", self)
        self.dismiss_btn.setStyleSheet("""
            QPushButton {
                background: rgba(20, 20, 24, 230); color: white;
                border: 2px solid white; border-radius: 10px;
                padding: 10px 18px; font-size: 14px; font-weight: 600;
            }
            QPushButton:hover { background: rgba(40, 40, 48, 230); }
        """)
        self.dismiss_btn.clicked.connect(self._dismiss)
        self.dismiss_btn.adjustSize()
        margin = 24
        self.dismiss_btn.move(
            full.width() - self.dismiss_btn.width() - margin,
            full.height() - self.dismiss_btn.height() - margin,
        )

        # Pulse animation
        if pulse:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._on_pulse_tick)
            self._timer.start(50)   # 20 FPS
        else:
            self._timer = None

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Auto-dismiss after 30s
        QTimer.singleShot(30_000, self._dismiss)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._dismiss()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        # Click anywhere except the dismiss button dismisses.
        if not self.dismiss_btn.geometry().contains(event.position().toPoint()):
            self._dismiss()

    def _on_pulse_tick(self) -> None:
        import math
        self._pulse_phase = (self._pulse_phase + 0.08) % (2 * math.pi)
        self.update()

    def _dismiss(self) -> None:
        if self._timer:
            self._timer.stop()
        self.hide()
        self.dismissed.emit()
        self.deleteLater()

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        origin = self.geometry().topLeft()
        # Convert all target rects to widget-local coords
        local_rects: list[QRectF] = []
        pad = 12
        for t in self.targets:
            tx, ty, tw, th = t.rect
            tx -= origin.x()
            ty -= origin.y()
            local_rects.append(QRectF(tx - pad, ty - pad, tw + 2 * pad, th + 2 * pad))

        # 1. Dim the rest of the screen — punch holes for every target.
        if self.dim_background:
            outer = QPainterPath()
            outer.addRect(QRectF(self.rect()))
            for r in local_rects:
                hole = QPainterPath()
                hole.addRoundedRect(r, 8, 8)
                outer.addPath(hole)
            outer.setFillRule(Qt.FillRule.OddEvenFill)
            dim = QColor(0, 0, 0, int(255 * DIM_OPACITY))
            p.fillPath(outer, dim)

        # 2. Outlines on each target.
        if self._timer:
            pulse_offset = (math.sin(self._pulse_phase) + 1) / 2 * 4
        else:
            pulse_offset = 0
        w = self.outline_width + pulse_offset

        for r in local_rects:
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Outer black halo
            p.setPen(QPen(QColor(0, 0, 0, 230), w + 6, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawRoundedRect(r, 8, 8)
            # Bright middle
            p.setPen(QPen(self.outline_color, w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawRoundedRect(r, 8, 8)
            # White inner
            p.setPen(QPen(QColor(255, 255, 255, 200), 1.5, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawRoundedRect(r.adjusted(w / 2, w / 2, -w / 2, -w / 2), 6, 6)

        # 3. Number badges — only when multiple targets
        if len(self.targets) > 1:
            self._draw_number_badges(p, local_rects)

        # 4. Combined caption panel
        self._draw_combined_caption(p, local_rects)

    def _draw_number_badges(self, p: QPainter, rects: list[QRectF]) -> None:
        font = QFont("Segoe UI", 18, QFont.Weight.Bold)
        p.setFont(font)
        fm = p.fontMetrics()
        radius = 22

        for i, r in enumerate(rects, start=1):
            # Anchor at the top-left corner of the outline rect, overlapping it
            cx = r.left()
            cy = r.top()
            badge_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

            p.setPen(QPen(QColor(0, 0, 0, 230), 3))
            p.setBrush(QBrush(self.outline_color))
            p.drawEllipse(badge_rect)

            p.setPen(QColor(0, 0, 0))
            text = str(i)
            tw = fm.horizontalAdvance(text)
            p.drawText(
                int(badge_rect.center().x() - tw / 2),
                int(badge_rect.center().y() + fm.ascent() / 2 - 2),
                text,
            )

    def _draw_combined_caption(self, p: QPainter, rects: list[QRectF]) -> None:
        """Single caption panel listing all correct answers."""
        # Build lines
        n = len(self.targets)
        title_line = "Correct answer:" if n == 1 else f"Correct answers  ({n} total)"
        answer_lines: list[str] = []
        for i, t in enumerate(self.targets, start=1):
            prefix = f"{i}.  " if n > 1 else ""
            label = t.label
            if len(label) > 80:
                label = label[:77] + "…"
            answer_lines.append(f"{prefix}{label}")

        # Measure
        title_font = QFont("Segoe UI", CAPTION_FONT_SIZE, QFont.Weight.Bold)
        body_font = QFont("Segoe UI", CAPTION_FONT_SIZE - 2, QFont.Weight.Normal)

        p.setFont(title_font)
        title_fm = p.fontMetrics()
        p.setFont(body_font)
        body_fm = p.fontMetrics()

        widths = [title_fm.horizontalAdvance(title_line)]
        widths.extend(body_fm.horizontalAdvance(ln) for ln in answer_lines)
        text_w = max(widths)
        title_h = title_fm.height() + 8
        line_h = body_fm.height() + 6
        body_h = line_h * len(answer_lines)
        text_h = title_h + body_h

        pad_x, pad_y = 22, 16
        cap_w = min(text_w + pad_x * 2, self.width() - 48)
        cap_h = text_h + pad_y * 2

        cap_rect = self._pick_caption_position(rects, cap_w, cap_h)

        # Background
        p.setPen(QPen(self.outline_color, 3))
        p.setBrush(QBrush(QColor(15, 15, 20, 240)))
        p.drawRoundedRect(cap_rect, 14, 14)

        # Title
        p.setFont(title_font)
        p.setPen(QColor(255, 255, 255))
        y = cap_rect.top() + pad_y + title_fm.ascent()
        p.drawText(int(cap_rect.left() + pad_x), int(y), title_line)
        y += title_h - title_fm.ascent() + body_fm.ascent() - 4

        # Answers
        p.setFont(body_font)
        for ln in answer_lines:
            p.drawText(int(cap_rect.left() + pad_x), int(y), ln)
            y += line_h

        self._draw_confidence_chip(p, cap_rect)

    def _pick_caption_position(self, rects: list[QRectF], cap_w: float, cap_h: float) -> QRectF:
        """Pick the screen corner that overlaps the fewest highlights.

        Reserve bottom space for the Dismiss button (~60px).
        """
        margin = 24
        btm_reserve = 60
        candidates = [
            QRectF(margin, margin, cap_w, cap_h),                                                # TL
            QRectF(self.width() - cap_w - margin, margin, cap_w, cap_h),                          # TR
            QRectF(margin, self.height() - cap_h - margin - btm_reserve, cap_w, cap_h),           # BL
            QRectF(self.width() - cap_w - margin,
                   self.height() - cap_h - margin - btm_reserve, cap_w, cap_h),                   # BR
        ]
        def overlap_count(candidate: QRectF) -> int:
            return sum(1 for r in rects if candidate.intersects(r))
        # Stable sort prefers earlier candidates (top-left first) on ties.
        candidates.sort(key=overlap_count)
        return candidates[0]

    def _draw_confidence_chip(self, p: QPainter, cap_rect: QRectF) -> None:
        chip_text = f"Confidence: {self.confidence}"
        chip_font = QFont("Segoe UI", 11, QFont.Weight.Bold)
        p.setFont(chip_font)
        cfm = p.fontMetrics()
        chip_w = cfm.horizontalAdvance(chip_text) + 16
        chip_h = cfm.height() + 8
        chip_rect = QRectF(
            cap_rect.right() - chip_w - 8,
            cap_rect.top() - chip_h / 2,
            chip_w, chip_h,
        )
        chip_color = {
            "high": QColor(56, 178, 90),
            "medium": QColor(232, 156, 78),
            "low": QColor(232, 92, 92),
        }.get(self.confidence, QColor(120, 120, 130))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(chip_color))
        p.drawRoundedRect(chip_rect, chip_h / 2, chip_h / 2)
        p.setPen(QColor(255, 255, 255))
        p.drawText(
            int(chip_rect.left() + 8),
            int(chip_rect.top() + chip_h / 2 + cfm.ascent() / 2 - 2),
            chip_text,
        )
