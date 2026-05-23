"""Qt UI: floating answer panel and unobtrusive auto-suggest toasts.

Polished look without heavy dependencies — pure PySide6, custom stylesheet,
frameless windows with rounded corners and drop shadows.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QPropertyAnimation, QTimer, Signal, QEasingCurve
from PySide6.QtGui import QColor, QGuiApplication, QFont, QPainter, QPainterPath, QBrush
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)


# Shared dark stylesheet — same vibe across all surfaces.
STYLE = """
* { color: #E6E6E6; font-family: "Segoe UI", "Inter", sans-serif; }
#card { background: #1E1F23; border-radius: 14px; }
#title { font-size: 13px; font-weight: 600; color: #A9B1BD; }
#body { background: transparent; border: none; font-size: 14px; line-height: 1.5; }
QLineEdit {
    background: #2A2C32; border: 1px solid #3A3D45; border-radius: 8px;
    padding: 8px 12px; font-size: 14px;
}
QLineEdit:focus { border-color: #6B8AFF; }
QPushButton {
    background: #2A2C32; border: 1px solid #3A3D45; border-radius: 8px;
    padding: 6px 14px; font-size: 13px;
}
QPushButton:hover { background: #353841; }
QPushButton#primary { background: #4F6BED; border: none; }
QPushButton#primary:hover { background: #6079F0; }
"""


class Card(QWidget):
    """Frameless, rounded, dropshadowed base widget. Draggable by its header."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self._drag_pos: QPoint | None = None

    def paintEvent(self, _):
        # Manual rounded-rect paint so shadow effect doesn't clip awkwardly.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(12, 12, -12, -12), 14, 14)
        p.fillPath(path, QBrush(QColor("#1E1F23")))

    def add_shadow(self) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.setGraphicsEffect(shadow)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, _):
        self._drag_pos = None


class AnswerPanel(Card):
    """Main floating panel — shows the LLM's streaming response + follow-up input."""

    ask_followup = Signal(str)
    capture_new = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__()
        # The provider's display name, shown next to user messages in the
        # chat transcript. Set by the app whenever the active provider changes.
        self.ai_name: str = "AI"
        self.resize(520, 380)
        self.add_shadow()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)

        header = QHBoxLayout()
        self.title = QLabel("ScreenSense")
        self.title.setObjectName("title")
        header.addWidget(self.title)
        header.addStretch()
        self.recapture_btn = QPushButton("Recapture")
        self.recapture_btn.clicked.connect(self.capture_new.emit)
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedWidth(28)
        self.close_btn.clicked.connect(self._on_close)
        header.addWidget(self.recapture_btn)
        header.addWidget(self.close_btn)
        outer.addLayout(header)

        self.body = QTextEdit()
        self.body.setObjectName("body")
        self.body.setReadOnly(True)
        f = QFont("Segoe UI", 11)
        self.body.setFont(f)
        outer.addWidget(self.body, 1)

        bottom = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask a follow-up…")
        self.input.returnPressed.connect(self._send)
        self.send_btn = QPushButton("Ask")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(self._send)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.send_btn)
        outer.addLayout(bottom)

        self._anchor_bottom_right()

    def _anchor_bottom_right(self) -> None:
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    def show_thinking(self, status: str = "Analyzing screen…") -> None:
        self.title.setText(status)
        self.body.clear()
        self.send_btn.setEnabled(False)
        self.input.setEnabled(False)
        self.show()
        self.raise_()

    def append_chunk(self, text: str) -> None:
        self.body.moveCursor(self.body.textCursor().MoveOperation.End)
        self.body.insertPlainText(text)

    def finish(self) -> None:
        self.title.setText("ScreenSense")
        self.send_btn.setEnabled(True)
        self.input.setEnabled(True)
        self.input.setFocus()

    def show_error(self, msg: str) -> None:
        self.title.setText("Error")
        self.body.setPlainText(msg)
        self.send_btn.setEnabled(True)
        self.input.setEnabled(True)

    def _send(self) -> None:
        q = self.input.text().strip()
        if not q:
            return
        self.input.clear()
        self.body.append(f"\n\n— You —\n{q}\n\n— {self.ai_name} —\n")
        self.send_btn.setEnabled(False)
        self.ask_followup.emit(q)

    def _on_close(self) -> None:
        self.hide()
        self.closed.emit()


class Toast(Card):
    """Small auto-suggest popup. Fades in/out, dismissible, click-to-act."""

    clicked = Signal()

    def __init__(self, message: str, action_label: str = "Analyze"):
        super().__init__()
        self.resize(340, 90)
        self.add_shadow()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        self.label = QLabel(message)
        self.label.setWordWrap(True)
        self.action = QPushButton(action_label)
        self.action.setObjectName("primary")
        self.action.clicked.connect(self.clicked.emit)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.action)

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.top() + 24)

        # auto-dismiss after 8s
        QTimer.singleShot(8000, self.fade_out)

        self.setWindowOpacity(0.0)
        self.anim_in = QPropertyAnimation(self, b"windowOpacity")
        self.anim_in.setDuration(180)
        self.anim_in.setStartValue(0.0)
        self.anim_in.setEndValue(1.0)
        self.anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)

    def show(self) -> None:
        super().show()
        self.anim_in.start()

    def fade_out(self) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(220)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self.close)
        anim.start()
        self._anim_out = anim  # keep reference
