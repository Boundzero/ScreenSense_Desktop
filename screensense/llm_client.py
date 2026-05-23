"""Provider-agnostic LLM client.

This module wraps a Provider in QThread-based workers so the UI can stream
responses without blocking. The provider abstraction handles the
per-vendor SDK differences; this layer handles Qt threading.

This replaces the old claude_client.py — the public API is the same
(make_client, ask_about_screen, ask_followup, Conversation), so callers
need only minor changes.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from .providers import Conversation, Provider, Turn, make_provider
from .watcher import to_png_bytes


SYSTEM_PROMPT = """You are ScreenSense, a desktop assistant that analyzes screenshots.
The user has captured what's currently on their screen and wants your help.

Be concise and direct. When you see:
- Error codes: explain what they mean and likely causes
- Charts/dashboards: summarize what the data shows, call out trends or anomalies
- Code/tracebacks: identify the failing line and probable fix
- UI screens: describe what's visible and answer specific questions about it

If the user follows up, they're referring to the same screen unless they say otherwise."""


class LLMWorker(QObject):
    """Streams a chat completion in a worker thread."""

    chunk = Signal(str)         # streaming text chunk
    finished = Signal(str)      # full response text
    failed = Signal(str)        # error message

    def __init__(self, provider: Provider, conversation: Conversation):
        super().__init__()
        self.provider = provider
        self.conversation = conversation

    def run(self) -> None:
        try:
            full = []
            for chunk in self.provider.stream(self.conversation, SYSTEM_PROMPT):
                full.append(chunk)
                self.chunk.emit(chunk)
            self.finished.emit("".join(full))
        except Exception as e:
            from .providers import humanize_provider_error
            msg = humanize_provider_error(
                e, self.provider.display_name, self.provider.model,
            )
            self.failed.emit(msg)


def make_client(provider_id: str, api_key: str, model: str, **extra) -> Provider:
    """Create a provider instance. Compatible name with the old single-provider API.

    Extra kwargs (e.g. base_url for self-hosted) are passed through.
    """
    return make_provider(provider_id, api_key, model, **extra)


def ask_about_screen(
    provider: Provider,
    conversation: Conversation,
    image,
    question: str = "",
) -> tuple[LLMWorker, QThread]:
    """Add a user turn (image + question) and kick off a streaming request."""
    png = to_png_bytes(image)
    conversation.turns.append(Turn(role="user", text=question, image_png=png))

    thread = QThread()
    worker = LLMWorker(provider, conversation)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return worker, thread


def ask_followup(
    provider: Provider,
    conversation: Conversation,
    question: str,
) -> tuple[LLMWorker, QThread]:
    """Follow-up question with no new screenshot — references the prior one."""
    conversation.turns.append(Turn(role="user", text=question))
    thread = QThread()
    worker = LLMWorker(provider, conversation)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return worker, thread
