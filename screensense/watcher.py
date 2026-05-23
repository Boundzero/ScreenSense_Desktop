"""Background screen watcher.

Captures the screen at low FPS, uses perceptual hashing to detect *meaningful*
changes (not cursor jiggles), and optionally runs a cheap local OCR scan to
flag "interesting" content like error codes or stack traces.

Only emits signals — does not call the Claude API directly.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

import imagehash
import mss
from PIL import Image
from PySide6.QtCore import QObject, QThread, Signal


# Patterns that suggest the screen contains something a user might want analyzed.
INTERESTING_PATTERNS = [
    re.compile(r"\b0x[0-9A-Fa-f]{4,}\b"),                 # hex error codes
    re.compile(r"\berror\s*[:#]?\s*\d+\b", re.I),          # "Error 404", "Error: 1234"
    re.compile(r"\bexception\b", re.I),
    re.compile(r"\btraceback\b", re.I),
    re.compile(r"\bstack\s*trace\b", re.I),
    re.compile(r"\bfailed\b", re.I),
    re.compile(r"\bSTOP\s*:?\s*0x", re.I),                # BSOD codes
    re.compile(r"\bE[A-Z_]{3,}\b"),                       # ENOENT, EACCES, etc.
]


# Patterns that suggest the screen contains a question with answer choices.
QUIZ_PATTERNS = [
    re.compile(r"^\s*[A-D]\s*[\.\):]", re.M),             # "A. foo", "B) bar", "C: baz"
    re.compile(r"^\s*[1-4]\s*[\.\):]", re.M),             # "1. foo", "2) bar"
    re.compile(r"\?\s*$", re.M),                          # lines ending in "?"
    re.compile(r"\bwhich of the following\b", re.I),
    re.compile(r"\bchoose (the|one|all)\b", re.I),
    re.compile(r"\bcorrect answer\b", re.I),
]


@dataclass
class OcrBox:
    """A single recognized text region with its on-screen position."""
    text: str
    # Bounding box in *screen* coordinates (not the resized OCR image).
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


@dataclass
class Frame:
    image: Image.Image
    timestamp: float
    interesting_hits: list[str] = field(default_factory=list)
    ocr_boxes: list[OcrBox] = field(default_factory=list)   # populated on demand
    quiz_likely: bool = False


def to_png_bytes(img: Image.Image, max_side: int = 1568) -> bytes:
    """Resize to Claude's recommended max dimension and encode as PNG."""
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


class ScreenWatcher(QObject):
    """Runs in a QThread, emits when something changes meaningfully."""

    significant_change = Signal(object)      # emits Frame
    interesting_detected = Signal(object)    # emits Frame when patterns hit

    def __init__(self, fps: float = 2.0, hash_threshold: int = 8):
        super().__init__()
        self.fps = fps
        self.hash_threshold = hash_threshold  # Hamming distance to consider "changed"
        self._running = False
        self._last_hash: Optional[imagehash.ImageHash] = None
        self._last_interesting_emit = 0.0

    def stop(self) -> None:
        self._running = False

    def grab_now(self) -> Optional["Frame"]:
        """Synchronous immediate capture, bypassing the loop.

        Used by the 'ask now' / 'quiz now' flows so they never have to wait
        for the background watcher. Also used as a diagnostic — if this works
        but the loop doesn't, the loop is broken; if this fails, screen
        capture itself is broken.
        """
        try:
            with mss.mss() as sct:
                monitor = sct.monitors[0]
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, raw.rgb)
                return Frame(image=img, timestamp=time.time())
        except Exception as e:
            print(f"[ScreenSense] grab_now failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            return None

    def run(self) -> None:
        self._running = True
        interval = 1.0 / self.fps
        consecutive_failures = 0

        while self._running:
            start = time.monotonic()
            try:
                with mss.mss() as sct:
                    monitor = sct.monitors[0]
                    raw = sct.grab(monitor)
                    img = Image.frombytes("RGB", raw.size, raw.rgb)
            except Exception as e:
                consecutive_failures += 1
                # Log the first few failures loudly so the user can see what's wrong.
                if consecutive_failures <= 3:
                    print(
                        f"[ScreenSense] watcher capture failed (#{consecutive_failures}): "
                        f"{type(e).__name__}: {e}",
                        file=sys.stderr,
                    )
                # Back off, but keep trying — display configs change (lid open/close,
                # external monitor plugged/unplugged, RDP session, etc.)
                time.sleep(min(2.0, 0.5 * consecutive_failures))
                continue

            consecutive_failures = 0

            try:
                # Always emit the first frame, even if hash says "no change",
                # so the UI has something to capture immediately on startup.
                first_frame = self._last_hash is None

                h = imagehash.phash(img, hash_size=16)
                if not first_frame and (h - self._last_hash) < self.hash_threshold:
                    self._sleep_remaining(start, interval)
                    continue
                self._last_hash = h

                frame = Frame(image=img, timestamp=time.time())
                self.significant_change.emit(frame)

                # Gate 2: OCR + regex. Only fires the "auto-suggest" path.
                # Throttle to once every 5s so we don't spam the user.
                if time.monotonic() - self._last_interesting_emit > 5.0:
                    hits = self._scan_for_interesting(img)
                    if hits:
                        frame.interesting_hits = hits
                        self._last_interesting_emit = time.monotonic()
                        self.interesting_detected.emit(frame)
            except Exception as e:
                print(
                    f"[ScreenSense] watcher processing failed: {type(e).__name__}: {e}",
                    file=sys.stderr,
                )

            self._sleep_remaining(start, interval)

    def _scan_for_interesting(self, img: Image.Image) -> list[str]:
        """Quick scan for error patterns. Lighter than full OCR — text only."""
        boxes = self._run_ocr(img)
        if not boxes:
            return []
        text = " ".join(b.text for b in boxes)
        hits = []
        for pat in INTERESTING_PATTERNS:
            m = pat.search(text)
            if m:
                hits.append(m.group(0))
        return hits

    def ocr_with_positions(self, img: Image.Image) -> list[OcrBox]:
        """Run OCR and return boxes in *original-image* coordinates.

        Safe to call from any thread — see ocr.py for the per-thread engine
        binding that prevents onnxruntime cross-thread crashes.
        """
        return self._run_ocr(img)

    @staticmethod
    def looks_like_quiz(boxes: list[OcrBox]) -> bool:
        """Heuristic: does this screen look like a multiple-choice question?"""
        text = "\n".join(b.text for b in boxes)
        hits = sum(1 for pat in QUIZ_PATTERNS if pat.search(text))
        return hits >= 2

    @staticmethod
    def _run_ocr(img: Image.Image) -> list[OcrBox]:
        """Thread-safe OCR via the per-thread engine pool in ocr.py."""
        from .ocr import ocr_image_with_positions
        return ocr_image_with_positions(img)

    @staticmethod
    def _sleep_remaining(start: float, interval: float) -> None:
        elapsed = time.monotonic() - start
        if elapsed < interval:
            time.sleep(interval - elapsed)


def start_watcher(fps: float = 2.0) -> tuple[ScreenWatcher, QThread]:
    """Create a watcher running in its own QThread. Caller owns lifecycle."""
    thread = QThread()
    watcher = ScreenWatcher(fps=fps)
    watcher.moveToThread(thread)
    thread.started.connect(watcher.run)
    return watcher, thread
