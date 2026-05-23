"""Thread-safe OCR helper.

onnxruntime sessions are not safe to call from a thread other than the one
that created them — doing so causes access-violation crashes on Windows.

This helper maintains a per-thread OCR engine. The first call from a given
thread creates an engine bound to that thread. Subsequent calls from the
same thread reuse it. Calls from other threads create their own engine.

This is the right tradeoff for ScreenSense: we have at most two threads
that do OCR (the watcher's auto-suggest scan, and the on-demand quiz worker),
so we end up with at most two engine instances — a modest memory cost in
exchange for crash-free behavior.
"""
from __future__ import annotations

import threading
from typing import Optional, TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from .watcher import OcrBox


_engines: dict[int, object] = {}
_engines_lock = threading.Lock()


def _get_or_create_engine() -> Optional[object]:
    """Return the OCR engine bound to the *calling* thread, creating it if needed."""
    tid = threading.get_ident()
    with _engines_lock:
        if tid in _engines:
            return _engines[tid]
    # Outside the lock: import + construction can be slow, and we don't want
    # to block other threads' lookups while this one initializes.
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
    except Exception:
        engine = None
    with _engines_lock:
        # Race-check: another thread might have populated this slot. Unlikely
        # (only one thread per tid by definition) but harmless to handle.
        _engines.setdefault(tid, engine)
        return _engines[tid]


def ocr_image_with_positions(img: Image.Image) -> list["OcrBox"]:
    """Run OCR on a PIL image and return boxes in the image's coordinate space.

    Safe to call from any thread. The engine is created lazily per-thread.
    """
    from .watcher import OcrBox

    engine = _get_or_create_engine()
    if engine is None:
        return []

    # Downscale for OCR speed, remember the scale factor to map boxes back.
    orig_w, orig_h = img.size
    small = img.copy()
    small.thumbnail((1280, 720), Image.LANCZOS)
    scale_x = orig_w / small.width
    scale_y = orig_h / small.height

    try:
        result, _ = engine(small)
    except Exception:
        return []
    if not result:
        return []

    boxes: list[OcrBox] = []
    for entry in result:
        if len(entry) < 2:
            continue
        poly, text = entry[0], entry[1]
        if not text or not poly:
            continue
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x_min, y_min = min(xs), min(ys)
        x_max, y_max = max(xs), max(ys)
        boxes.append(OcrBox(
            text=text,
            x=int(x_min * scale_x),
            y=int(y_min * scale_y),
            width=int((x_max - x_min) * scale_x),
            height=int((y_max - y_min) * scale_y),
        ))
    return boxes
