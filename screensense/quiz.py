"""Quiz mode — identify correct answer(s) to a question on screen and locate them.

Strategy:
  1. OCR the screen with positional data (already done by the watcher).
  2. Ask Claude to identify the correct answer(s), returning a structured
     response. Handles single-answer, "select all that apply", and
     "select N" questions.
  3. Match each answer's text against our OCR boxes to find pixel coordinates.
  4. Hand the list of bounding rects to the highlight overlay.

The text-matching step is robust to minor OCR errors (whitespace, casing,
small character substitutions). If matching fails for an answer, we still
show it in the chat panel — the user just doesn't get a highlight for that one.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from PIL import Image
from PySide6.QtCore import QObject, QThread, Signal

from .providers import Provider
from .watcher import OcrBox, to_png_bytes


QUIZ_SYSTEM_PROMPT = """You are an exam-helper analyzing a screenshot. Your job
is to identify the correct answer(s) to a question shown in the image.

# When to set found_question

Set found_question: TRUE in ALL of these situations:
  - You see a question with lettered choices (A, B, C, D…)
  - You see a question with numbered choices (1, 2, 3, 4…)
  - You see selectable options with bullets, radio buttons, or checkboxes
  - You see a fill-in-the-blank with selectable options
  - You see ANY test, quiz, exam, survey, or poll content
  - You see what LOOKS like answer choices even if the question stem is
    cut off by the crop — pick the most likely correct one and mark it
    confidence "low"
  - You see a coding question with multiple solution choices
  - You see a medical, legal, technical, or any subject-matter question

Set found_question: FALSE ONLY in these specific situations:
  - The image is a chat window, email, or social media feed
  - The image shows only prose text (article, blog post, documentation)
  - The image shows only an application UI with no test content
  - The image is mostly blank or shows a desktop wallpaper
  - You genuinely see NOTHING that looks like answer choices

# Crucial rule

If you can see ANY indication of selectable answers — even partial, even
unclear, even with a cut-off stem — set found_question to TRUE and pick the
most likely answer. Mark confidence as "low" when uncertain. Users find a
low-confidence guess far more useful than "found_question: false".

# Question types

  - "single"     — one correct answer (the default)
  - "select_n"   — explicitly asks for N answers ("select two", "choose 3")
  - "select_all" — "select all that apply", number unspecified

# Required output format

Respond with ONLY a JSON object — no prose before or after, no markdown:

{
  "found_question": true | false,
  "question": "a short summary of the question — 20 words max, NOT a verbatim transcription. Long scenarios should be condensed.",
  "question_type": "single" | "select_n" | "select_all",
  "answers": [
    {
      "letter": "A" | "B" | "1" | etc. (or null if not letter-keyed),
      "text": "the exact verbatim text of this correct answer as it appears on screen",
      "rationale": "1 sentence on why this answer is correct"
    }
  ],
  "overall_explanation": "1-2 sentence summary (optional)",
  "confidence": "high" | "medium" | "low"
}

# Answer text formatting (CRITICAL)

For each answer's "text" field: copy it EXACTLY as it appears on screen,
including any prefix like "A." or "1)". This is essential — the app uses
this text to locate and highlight the answer on the user's screen via OCR
matching. If the on-screen text is "B. Photosynthesis", write exactly
"B. Photosynthesis" — not "Photosynthesis" alone, not "Option B".

# Count rule

  - For "single" questions: the answers array has EXACTLY ONE entry
  - For "select_n" questions: EXACTLY N entries
  - For "select_all": every correct answer, in screen order (top-to-bottom)"""


@dataclass
class AnswerMatch:
    """One correct answer plus its located position on screen."""
    letter: Optional[str]
    text: str
    rationale: str
    # Set after OCR matching:
    highlight_rect: Optional[tuple[int, int, int, int]] = None
    match_quality: str = "none"


@dataclass
class QuizResult:
    found_question: bool
    question: str
    question_type: str    # "single" | "select_n" | "select_all"
    answers: list[AnswerMatch] = field(default_factory=list)
    overall_explanation: str = ""
    confidence: str = "low"

    @property
    def answer_count(self) -> int:
        return len(self.answers)

    @property
    def matched_count(self) -> int:
        return sum(1 for a in self.answers if a.highlight_rect is not None)


def _normalize(s: str) -> str:
    """Aggressive normalization for fuzzy text matching."""
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip()


def locate_answer(answer_text: str, letter: Optional[str], ocr_boxes: list[OcrBox]) -> tuple[Optional[tuple[int, int, int, int]], str]:
    """Find the OCR box(es) matching the correct answer.

    Returns (bbox, quality). bbox is (x, y, w, h) in screen coords or None.
    """
    if not ocr_boxes:
        return None, "none"

    norm_target = _normalize(answer_text)
    if not norm_target:
        return None, "none"

    # Strip the leading letter prefix from the target if present, since OCR
    # may split "A. Paris" into two boxes ("A." and "Paris").
    target_no_prefix = re.sub(r"^[a-d0-9][\.\):]\s*", "", norm_target).strip()

    # Strategy 1: find a single OCR box that contains most of the answer text.
    best_box: Optional[OcrBox] = None
    best_score = 0.0
    for box in ocr_boxes:
        norm_box = _normalize(box.text)
        if not norm_box:
            continue
        # Score = fraction of target words present in this box.
        target_words = set(target_no_prefix.split())
        if not target_words:
            continue
        matched = sum(1 for w in target_words if w in norm_box)
        score = matched / len(target_words)
        if score > best_score:
            best_score = score
            best_box = box

    if best_box is not None and best_score >= 0.7:
        return ((best_box.x, best_box.y, best_box.width, best_box.height), "exact")

    # Strategy 2: find a sequence of boxes on roughly the same row that together
    # contain the target. Useful when OCR splits "B. The Pacific Ocean" into
    # multiple boxes side-by-side.
    if letter:
        prefix_pattern = re.compile(rf"^\s*{re.escape(letter)}\s*[\.\):]\s*", re.I)
        prefix_box = next(
            (b for b in ocr_boxes if prefix_pattern.match(b.text)), None
        )
        if prefix_box:
            # Collect boxes on the same row (overlapping vertically with prefix_box)
            row_boxes = sorted(
                [b for b in ocr_boxes if _rows_overlap(b, prefix_box)],
                key=lambda b: b.x,
            )
            if row_boxes:
                x = min(b.x for b in row_boxes)
                y = min(b.y for b in row_boxes)
                right = max(b.right for b in row_boxes)
                bottom = max(b.bottom for b in row_boxes)
                return ((x, y, right - x, bottom - y), "partial")

    # Strategy 3: partial best-box match (50%+ words)
    if best_box is not None and best_score >= 0.5:
        return ((best_box.x, best_box.y, best_box.width, best_box.height), "partial")

    return None, "none"


def _rows_overlap(a: OcrBox, b: OcrBox) -> bool:
    """Two boxes are on the same row if their vertical spans overlap by 50%+."""
    overlap_top = max(a.y, b.y)
    overlap_bottom = min(a.bottom, b.bottom)
    overlap = max(0, overlap_bottom - overlap_top)
    smaller_height = min(a.height, b.height)
    return smaller_height > 0 and overlap / smaller_height >= 0.5


class QuizWorker(QObject):
    """Calls the LLM with the screenshot, parses the JSON response."""

    finished = Signal(object)   # emits QuizResult
    failed = Signal(str)

    def __init__(self, provider: Provider, image: Image.Image, ocr_boxes: Optional[list[OcrBox]] = None):
        super().__init__()
        self.provider = provider
        self.image = image
        # If caller already has OCR boxes, use them. Otherwise we'll compute
        # them inside run() — on the worker thread, so the OCR engine ends up
        # bound to the worker thread rather than the main thread.
        self.ocr_boxes = ocr_boxes

    def run(self) -> None:
        import sys
        try:
            # Run OCR on the worker thread if the caller didn't pre-compute it.
            # This is critical for crash-free operation: onnxruntime sessions are
            # bound to the thread that created them.
            if self.ocr_boxes is None:
                from .ocr import ocr_image_with_positions
                self.ocr_boxes = ocr_image_with_positions(self.image)

            png = to_png_bytes(self.image)

            # Save the image we're about to send to disk for diagnostic purposes.
            # This is the ONLY way to confirm what the model actually received
            # when something looks wrong — image bytes can't be inspected from
            # logs alone.
            try:
                import time as _time
                from pathlib import Path
                if getattr(sys, "frozen", False):
                    diag_dir = Path(sys.executable).parent / "diagnostics"
                else:
                    diag_dir = Path.cwd() / "diagnostics"
                diag_dir.mkdir(exist_ok=True)
                # Keep only the last quiz capture — overwrite each time.
                diag_path = diag_dir / "last_quiz_capture.png"
                diag_path.write_bytes(png)
                print(f"[ScreenSense quiz] saved capture to {diag_path} "
                      f"(image size {self.image.size}, "
                      f"OCR found {len(self.ocr_boxes)} text boxes)",
                      file=sys.stderr, flush=True)
                # Log a sample of detected OCR text so we can tell what's
                # actually in the image even without opening the PNG.
                if self.ocr_boxes:
                    sample = " | ".join(b.text for b in self.ocr_boxes[:10])
                    print(f"[ScreenSense quiz] OCR sample: {sample[:300]}",
                          file=sys.stderr, flush=True)
            except Exception as _e:
                print(f"[ScreenSense quiz] couldn't save diagnostic: {_e}",
                      file=sys.stderr, flush=True)

            text = self.provider.complete_json(
                image_png=png,
                user_text="Analyze this question and return the JSON response.",
                system_prompt=QUIZ_SYSTEM_PROMPT,
                # 4000 tokens comfortably covers long technical questions (CCNA,
                # legal/medical scenarios, code review questions) with multiple
                # multi-line answer choices plus rationales. 1000 was too tight
                # and caused mid-string truncation that broke the JSON.
                max_tokens=4000,
            )

            # Log the raw response to stderr for debugging.
            print(f"[ScreenSense quiz] raw response from "
                  f"{self.provider.display_name}:\n{text[:2000]}\n---",
                  file=sys.stderr, flush=True)

            data = self._parse_json(text)
            if data is None:
                # Try to detect the most common failure mode: response was
                # truncated mid-output by hitting max_tokens. Symptoms:
                # opens a JSON object/string but never closes it.
                stripped = text.strip()
                looks_truncated = (
                    stripped.startswith("{")
                    and not stripped.rstrip().endswith("}")
                )

                # Build a preview that shows both beginning and end of the
                # response. Thinking models often emit reasoning at the start
                # and (sometimes) JSON at the end — a beginning-only preview
                # would hide whether the model ever got around to the JSON.
                if len(stripped) > 800:
                    preview = (
                        stripped[:400]
                        + "\n\n  […" + str(len(stripped) - 800) + " chars omitted…]\n\n"
                        + stripped[-400:]
                    )
                else:
                    preview = stripped

                # Also detect "all reasoning, no JSON at all" — when the
                # response has reasoning markers but never produces a JSON
                # object. Worth a distinct message since the fix is different
                # (disable thinking mode, not "try a smaller capture").
                looks_like_pure_reasoning = (
                    "{" not in stripped
                    and any(marker in stripped.lower() for marker in [
                        "the user", "the question", "let me", "analyze",
                        "first,", "step 1", "**1.", "the image",
                    ])
                )

                if looks_truncated:
                    self.failed.emit(
                        "The model's response was cut off before it finished.\n\n"
                        "This usually means the question is unusually long or "
                        "has many answer choices. Things to try:\n"
                        "  • Use region quiz on just the answer choices\n"
                        "  • Switch to a different model from the tray menu\n\n"
                        f"Response so far:\n{preview}"
                    )
                elif looks_like_pure_reasoning:
                    self.failed.emit(
                        "The model wrote reasoning but never produced the JSON "
                        "output.\n\n"
                        "This is common with thinking-mode models that "
                        "explain their answer in prose instead of structured "
                        "output. Try:\n"
                        "  • A non-thinking model (Llama 3.2 Vision, Gemma 3, "
                        "cloud Claude/GPT)\n"
                        "  • Disable thinking mode in Open WebUI's model "
                        "settings if available\n\n"
                        f"Response:\n{preview}"
                    )
                else:
                    self.failed.emit(
                        "Couldn't parse the model's response as JSON.\n\n"
                        "The model returned:\n"
                        f"{preview}\n\n"
                        "Try a different model from the tray menu's AI Provider submenu."
                    )
                return

            answers_raw = data.get("answers") or []
            answers: list[AnswerMatch] = []
            for raw in answers_raw:
                if not isinstance(raw, dict):
                    continue
                ans_text = str(raw.get("text") or "")
                if not ans_text:
                    continue
                answers.append(AnswerMatch(
                    letter=raw.get("letter"),
                    text=ans_text,
                    rationale=str(raw.get("rationale") or ""),
                ))

            result = QuizResult(
                found_question=bool(data.get("found_question", False)),
                question=str(data.get("question") or ""),
                question_type=str(data.get("question_type") or "single"),
                answers=answers,
                overall_explanation=str(data.get("overall_explanation") or ""),
                confidence=str(data.get("confidence") or "low"),
            )

            # Locate each answer independently
            if result.found_question:
                for ans in result.answers:
                    rect, quality = locate_answer(ans.text, ans.letter, self.ocr_boxes)
                    ans.highlight_rect = rect
                    ans.match_quality = quality

            self.finished.emit(result)
        except Exception as e:
            from .providers import humanize_provider_error
            msg = humanize_provider_error(
                e, self.provider.display_name, self.provider.model,
            )
            # Log the exception too, so the diagnostic log shows what went
            # wrong (not just the user-facing panel). Include both the
            # humanized message and the raw exception for full visibility.
            print(f"[ScreenSense quiz] FAILED: {type(e).__name__}: {e}\n"
                  f"Humanized message: {msg}",
                  file=sys.stderr, flush=True)
            self.failed.emit(msg)

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        """Extract JSON from the model's response, tolerating common quirks.

        Handles:
          - Plain JSON
          - Markdown-fenced JSON (```json ... ``` or ``` ... ```)
          - JSON preceded by chatty preamble ("Here is the JSON:\n{...}")
          - JSON followed by trailing prose
          - Whole response wrapped in a single ```...``` block
        """
        if not text:
            return None
        text = text.strip()

        # Try direct parse first — fastest path when the model behaved.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strip leading/trailing markdown fences if present.
        candidate = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text)
        candidate = re.sub(r"\n?\s*```\s*$", "", candidate).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        # Look for the FIRST opening brace and try to parse from there. Use a
        # balanced-brace scanner because lazy regex `\{.*?\}` cuts off at the
        # wrong place, and greedy `\{.*\}` grabs trailing junk after the JSON.
        start = text.find("{")
        while start != -1:
            obj = QuizWorker._extract_balanced_object(text, start)
            if obj is not None:
                try:
                    return json.loads(obj)
                except json.JSONDecodeError:
                    pass
            start = text.find("{", start + 1)

        return None

    @staticmethod
    def _extract_balanced_object(text: str, start: int) -> Optional[str]:
        """Given the index of an opening brace, return the matching balanced
        substring. Handles strings (and escapes) so braces inside JSON strings
        don't fool the counter.
        """
        if start >= len(text) or text[start] != "{":
            return None
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]
        return None


def run_quiz_analysis(
    provider: Provider,
    image: Image.Image,
    ocr_boxes: Optional[list[OcrBox]] = None,
) -> tuple[QuizWorker, QThread]:
    """Kick off quiz analysis. If ocr_boxes is None, OCR runs inside the worker thread.

    Passing None is the *safer* default: the OCR engine binds to the worker
    thread that created it, so there's no risk of cross-thread access crashes.
    """
    thread = QThread()
    worker = QuizWorker(provider, image, ocr_boxes)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return worker, thread
