"""LLM provider abstraction.

ScreenSense supports three providers — Anthropic Claude, OpenAI GPT, and
Google Gemini. Each has a different SDK and different image input format,
but our app only needs two operations:

  1. Stream a chat completion given a conversation (some turns include images)
  2. Get a structured JSON response given a single screenshot + prompt

This module defines a common Provider interface and three implementations.
The app code talks to Provider instances and never touches the underlying SDKs.

Adding a new provider:
  - Subclass Provider
  - Implement stream() and complete_json()
  - Register it in PROVIDERS at the bottom
  - Add a Config field for its API key
"""
from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Optional


# ---- Shared message format used by ScreenSense internally ----

@dataclass
class Turn:
    """One turn in a multi-turn conversation."""
    role: str             # "user" or "assistant"
    text: str
    image_png: Optional[bytes] = None   # raw PNG bytes; user turns only


@dataclass
class Conversation:
    turns: list[Turn] = field(default_factory=list)


# ---- Model catalogs ----
# Keep these in sync with each provider's docs. Vision-capable models only.
# Updated May 2026.

# Browser-like User-Agent string. Used for self-hosted providers behind
# Cloudflare — Cloudflare's Browser Integrity Check (BIC) returns Error 1010
# when it sees a Python-urllib User-Agent. Sending a current-Chrome string
# bypasses that specific check without affecting Cloudflare Access auth.
# Update this string when Chrome major version advances by a year or two.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


ANTHROPIC_MODELS = [
    ("claude-opus-4-7",       "Claude Opus 4.7 — most capable, higher cost"),
    ("claude-sonnet-4-6",     "Claude Sonnet 4.6 — balanced, recommended default"),
    ("claude-haiku-4-5",      "Claude Haiku 4.5 — fastest, cheapest"),
]

OPENAI_MODELS = [
    ("gpt-5.5",               "GPT-5.5 — most capable"),
    ("gpt-5.4",               "GPT-5.4 — recommended balanced default"),
    ("gpt-5.4-mini",          "GPT-5.4 mini — cheaper, fast"),
    ("gpt-5.4-nano",          "GPT-5.4 nano — cheapest"),
    ("gpt-4o",                "GPT-4o — legacy, lower cost"),
]

GOOGLE_MODELS = [
    # Use the actual API model IDs (the `-preview` suffix is required on the
    # 3.x family). Marketing names like "Gemini 3 Flash" don't work directly.
    # The 2.5 family is stable and a safe fallback if 3.x preview models are
    # not enabled on a particular API key / region.
    ("gemini-3-flash-preview",  "Gemini 3 Flash  (recommended — balanced)"),
    ("gemini-3.1-pro-preview",  "Gemini 3.1 Pro  (most capable)"),
    ("gemini-2.5-flash",        "Gemini 2.5 Flash  (stable, no preview required)"),
    ("gemini-2.5-pro",          "Gemini 2.5 Pro  (stable, no preview required)"),
    ("gemini-2.5-flash-lite",   "Gemini 2.5 Flash-Lite  (cheapest stable)"),
]


# ---- Provider interface ----

class Provider(ABC):
    """Abstract base class. Concrete providers implement stream() and complete_json()."""

    name: str = ""           # short id, e.g. "anthropic"
    display_name: str = ""   # for UI, e.g. "Anthropic Claude"
    api_key_prefix: str = "" # expected key prefix for validation, "" if no convention
    needs_base_url: bool = False  # True for providers like Open WebUI / Ollama

    def __init__(self, api_key: str, model: str, **extra):
        self.api_key = api_key
        self.model = model
        # Subclasses that need extra config (e.g. base_url for self-hosted)
        # read from extra dict. Unknown keys ignored.
        self.extra = extra

    @abstractmethod
    def stream(
        self,
        conversation: Conversation,
        system_prompt: str,
        max_tokens: int = 1024,
    ) -> Iterator[str]:
        """Stream a chat completion. Yields text chunks as they arrive."""
        raise NotImplementedError

    @abstractmethod
    def complete_json(
        self,
        image_png: bytes,
        user_text: str,
        system_prompt: str,
        max_tokens: int = 1000,
    ) -> str:
        """Single-shot non-streaming call. Returns the raw text response.

        Used for quiz mode where we want the full JSON before parsing.
        """
        raise NotImplementedError


# ---- Anthropic implementation ----

class AnthropicProvider(Provider):
    name = "anthropic"
    display_name = "Anthropic Claude"
    api_key_prefix = "sk-ant-"

    def __init__(self, api_key: str, model: str, **extra):
        super().__init__(api_key, model, **extra)
        from anthropic import Anthropic
        self._client = Anthropic(api_key=api_key)

    def _to_messages(self, conversation: Conversation) -> list[dict]:
        out = []
        for t in conversation.turns:
            if t.role == "user" and t.image_png is not None:
                out.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": base64.b64encode(t.image_png).decode(),
                            },
                        },
                        {"type": "text", "text": t.text or "What's on this screen?"},
                    ],
                })
            else:
                out.append({"role": t.role, "content": t.text})
        return out

    def stream(self, conversation, system_prompt, max_tokens=1024):
        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=self._to_messages(conversation),
        ) as stream:
            for chunk in stream.text_stream:
                yield chunk

    def complete_json(self, image_png, user_text, system_prompt, max_tokens=1000):
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(image_png).decode(),
                        },
                    },
                    {"type": "text", "text": user_text},
                ],
            }],
        )
        return "".join(b.text for b in resp.content if hasattr(b, "text"))


# ---- OpenAI implementation ----

class OpenAIProvider(Provider):
    name = "openai"
    display_name = "OpenAI ChatGPT"
    api_key_prefix = "sk-"

    # Subclasses (Open WebUI, Ollama, etc.) override these.
    _default_base_url: Optional[str] = None    # None = use OpenAI cloud default
    _use_responses_api: bool = True             # only OpenAI cloud has this

    def __init__(self, api_key: str, model: str, **extra):
        super().__init__(api_key, model, **extra)
        from openai import OpenAI

        # Build the client. Self-hosted providers pass base_url; OpenAI cloud
        # leaves it None which means the SDK uses its built-in default.
        base_url = extra.get("base_url") or self._default_base_url
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        self._client = OpenAI(**kwargs)

    def _to_messages(self, conversation: Conversation, system_prompt: str) -> list[dict]:
        # OpenAI uses 'system' as the first message rather than a separate parameter.
        out: list[dict] = [{"role": "system", "content": system_prompt}]
        for t in conversation.turns:
            if t.role == "user" and t.image_png is not None:
                # OpenAI vision: content is a list of content parts, image is a data URL
                data_url = f"data:image/png;base64,{base64.b64encode(t.image_png).decode()}"
                out.append({
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": t.text or "What's on this screen?"},
                        {"type": "input_image", "image_url": data_url},
                    ],
                })
            else:
                out.append({"role": t.role, "content": t.text})
        return out

    def stream(self, conversation, system_prompt, max_tokens=1024):
        # Try the newer Responses API first; fall back to Chat Completions if needed.
        # Self-hosted/compatible servers (Open WebUI, Ollama, vLLM, LM Studio) don't
        # implement Responses — skip it for those.
        messages = self._to_messages(conversation, system_prompt)
        if self._use_responses_api:
            try:
                response = self._client.responses.create(
                    model=self.model,
                    input=messages,
                    max_output_tokens=max_tokens,
                    stream=True,
                )
                for event in response:
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str):
                        yield delta
                return
            except (AttributeError, TypeError):
                pass   # fall through
        yield from self._fallback_stream(messages, max_tokens)

    def _fallback_stream(self, messages, max_tokens):
        # Convert from Responses-style content parts to Chat-style
        legacy_msgs = []
        for m in messages:
            content = m["content"]
            if isinstance(content, list):
                new_content = []
                for part in content:
                    if part.get("type") == "input_text":
                        new_content.append({"type": "text", "text": part["text"]})
                    elif part.get("type") == "input_image":
                        new_content.append({"type": "image_url",
                                            "image_url": {"url": part["image_url"]}})
                legacy_msgs.append({"role": m["role"], "content": new_content})
            else:
                legacy_msgs.append(m)
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=legacy_msgs,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
            except (AttributeError, IndexError):
                continue

    def complete_json(self, image_png, user_text, system_prompt, max_tokens=1000):
        data_url = f"data:image/png;base64,{base64.b64encode(image_png).decode()}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "input_text", "text": user_text},
                {"type": "input_image", "image_url": data_url},
            ]},
        ]
        if self._use_responses_api:
            try:
                resp = self._client.responses.create(
                    model=self.model,
                    input=messages,
                    max_output_tokens=max_tokens,
                )
                output_text = getattr(resp, "output_text", None)
                if output_text:
                    return output_text
                parts = []
                for item in getattr(resp, "output", []):
                    for c in getattr(item, "content", []):
                        text = getattr(c, "text", None)
                        if text:
                            parts.append(text)
                return "".join(parts)
            except (AttributeError, TypeError):
                pass
        return self._fallback_complete_json(image_png, user_text, system_prompt, max_tokens)

    def _fallback_complete_json(self, image_png, user_text, system_prompt, max_tokens):
        data_url = f"data:image/png;base64,{base64.b64encode(image_png).decode()}"
        resp = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]},
            ],
        )
        return resp.choices[0].message.content or ""


# ---- Google Gemini implementation ----

class GoogleProvider(Provider):
    name = "google"
    display_name = "Google Gemini"
    api_key_prefix = ""   # Google keys don't have a fixed prefix

    def __init__(self, api_key: str, model: str, **extra):
        super().__init__(api_key, model, **extra)
        # google-genai is the current 2026 SDK (the older google-generativeai is legacy)
        try:
            from google import genai
            self._client = genai.Client(api_key=api_key)
            self._sdk = "new"
        except ImportError:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self._genai = genai
            self._sdk = "legacy"

    def _build_contents(self, conversation: Conversation) -> list[dict]:
        """Gemini wants contents as a list of {role, parts: [...]} dicts.

        role must be 'user' or 'model' (not 'assistant'). Each part is either
        {'text': '...'} or {'inline_data': {'mime_type': ..., 'data': base64}}.
        """
        out = []
        for t in conversation.turns:
            role = "user" if t.role == "user" else "model"
            parts = []
            if t.image_png is not None:
                parts.append({
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(t.image_png).decode(),
                    }
                })
            if t.text or t.image_png is None:
                parts.append({"text": t.text or "What's on this screen?"})
            out.append({"role": role, "parts": parts})
        return out

    def stream(self, conversation, system_prompt, max_tokens=1024):
        contents = self._build_contents(conversation)
        if self._sdk == "new":
            from google.genai import types
            response = self._client.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                ),
            )
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        else:
            model = self._genai.GenerativeModel(
                self.model, system_instruction=system_prompt,
            )
            response = model.generate_content(
                contents,
                stream=True,
                generation_config={"max_output_tokens": max_tokens},
            )
            for chunk in response:
                if chunk.text:
                    yield chunk.text

    def complete_json(self, image_png, user_text, system_prompt, max_tokens=1000):
        contents = [{
            "role": "user",
            "parts": [
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(image_png).decode(),
                    }
                },
                {"text": user_text},
            ],
        }]
        if self._sdk == "new":
            from google.genai import types
            response = self._client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                ),
            )
            return response.text or ""
        else:
            model = self._genai.GenerativeModel(
                self.model, system_instruction=system_prompt,
            )
            response = model.generate_content(
                contents,
                generation_config={
                    "max_output_tokens": max_tokens,
                    "response_mime_type": "application/json",
                },
            )
            return response.text or ""


# ---- Open WebUI implementation (and other OpenAI-compatible self-hosted servers) ----

class OpenWebUIProvider(OpenAIProvider):
    """OpenAI-compatible self-hosted LLM server.

    Open WebUI exposes an OpenAI-shaped API at /api/, which means we can reuse
    the entire OpenAIProvider stack and just swap the base URL. This same
    class also works for Ollama (via its OpenAI-compatible endpoint), vLLM,
    LM Studio, LocalAI, llama.cpp's server, and any other server that speaks
    OpenAI Chat Completions.

    Configuration requires both api_key AND base_url. The model catalog is
    fetched live from the server via list_models() rather than hardcoded —
    the user picks from whatever's actually loaded.
    """

    name = "openwebui"
    display_name = "Open WebUI (self-hosted)"
    api_key_prefix = ""   # users set their own keys, no fixed prefix
    needs_base_url = True
    # Self-hosted servers don't expose OpenAI's /v1/responses endpoint —
    # everything goes through chat.completions.
    _use_responses_api = False

    def __init__(self, api_key: str, model: str, **extra):
        base_url = extra.get("base_url", "").strip()
        if not base_url:
            raise ValueError(
                "Open WebUI requires a server URL (e.g. http://192.168.1.50:3000)"
            )
        # Open WebUI's OpenAI-compatible endpoint is at /api/. Tolerate users
        # passing either the bare host or already including the path.
        base_url = base_url.rstrip("/")
        if not base_url.endswith("/v1") and not base_url.endswith("/api"):
            # Append /api which Open WebUI uses; the OpenAI SDK will then
            # request /api/chat/completions which is what Open WebUI serves.
            base_url = base_url + "/api"
        extra["base_url"] = base_url
        super().__init__(api_key, model, **extra)

    # --- Override message-shaping for Open WebUI's particular quirks ---
    #
    # Open WebUI (and several of its backends like Ollama) expect `image_url`
    # as a plain string rather than as a {"url": ...} object. The OpenAI spec
    # technically supports both forms, but Open WebUI's image-handling code
    # only deals with the string form. Given the object form it throws
    # 'NoneType' object has no attribute 'startswith' from its internal
    # image preprocessor.

    def _is_thinking_model(self) -> bool:
        """Return True if the configured model is a known thinking model.

        Thinking models default to chain-of-thought reasoning in a separate
        'thinking' field. For structured-output tasks like quiz mode this
        eats context and we want them to skip straight to the answer.
        """
        model_lower = (self.model or "").lower()
        return (
            "qwen3" in model_lower
            or "deepseek" in model_lower
            or "r1" in model_lower
        )

    def _prepare_system_prompt(self, system_prompt: str) -> str:
        """Augment the system prompt for known thinking models.

        Qwen3/3.5 accepts /no_think as a directive that disables thinking.
        DeepSeek R1 respects similar phrasings. Adding it as a suffix is
        harmless on models that don't recognize it.
        """
        if self._is_thinking_model():
            return (
                system_prompt
                + "\n\n/no_think\n\nIMPORTANT: Skip all reasoning, analysis, "
                "and explanation. Respond with the JSON object ONLY. Do not "
                "write any text before or after the JSON. Do not use <think> "
                "tags. Begin your response with the opening brace { and end "
                "with the closing brace }."
            )
        return system_prompt

    def _prepare_user_text(self, user_text: str) -> str:
        """For thinking models, prepend /no_think to the user message too.

        Qwen3/3.5 sometimes only honors the directive when it appears in the
        user turn, not the system message. Belt and suspenders.
        """
        if self._is_thinking_model():
            return "/no_think " + user_text
        return user_text

    def _to_messages(self, conversation: Conversation, system_prompt: str) -> list[dict]:
        """Open WebUI message format.

        Uses the OBJECT form of image_url: {"url": "data:..."}. This is the
        canonical OpenAI Vision format that Open WebUI's image preprocessor
        expects when iterating message content parts. The string-shortcut form
        (image_url being a string directly) trips '.get' errors deeper in the
        OWUI/Ollama bridge code.
        """
        out: list[dict] = [{"role": "system", "content": self._prepare_system_prompt(system_prompt)}]
        for t in conversation.turns:
            if t.role == "user" and t.image_png is not None:
                data_url = f"data:image/png;base64,{base64.b64encode(t.image_png).decode()}"
                user_text = self._prepare_user_text(t.text or "What's on this screen?")
                out.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                })
            elif t.role == "user":
                out.append({"role": "user", "content": self._prepare_user_text(t.text)})
            else:
                out.append({"role": t.role, "content": t.text})
        return out

    def stream(self, conversation, system_prompt, max_tokens=1024):
        """Stream a chat completion via direct HTTP POST.

        Bypasses the OpenAI SDK so we have complete control over the request
        body — the SDK has historically reshuffled image fields in ways that
        broke Open WebUI compatibility. With direct HTTP, what we put in is
        what gets sent.

        Tries the OpenAI Vision format first, then falls back to Ollama-native
        format on the specific OWUI image preprocessor error.
        """
        messages_oai = self._to_messages(conversation, system_prompt)
        self._log_request(messages_oai, max_tokens, streaming=True, fmt="openai")
        try:
            for chunk in self._http_chat_openai(messages_oai, max_tokens, stream=True):
                yield chunk
            return
        except RuntimeError as e:
            if not self._is_owui_image_error(e):
                self._log_response_body_on_error(e)
                raise
            # Fall through to Ollama-native format
            print("[ScreenSense OWUI] OpenAI format rejected with image-preprocessor "
                  "error — retrying with Ollama-native format.",
                  file=__import__("sys").stderr, flush=True)

        messages_ollama = self._to_messages_ollama(conversation, system_prompt)
        self._log_request(messages_ollama, max_tokens, streaming=True, fmt="ollama")
        try:
            for chunk in self._http_chat_ollama(messages_ollama, max_tokens, stream=True):
                yield chunk
        except Exception as e:
            self._log_response_body_on_error(e)
            raise

    def complete_json(self, image_png, user_text, system_prompt, max_tokens=1000):
        """Non-streaming completion via direct HTTP POST.

        Tries OpenAI Vision format first, then falls back to Ollama-native.

        IMPLEMENTATION NOTE: although this method's *interface* is non-streaming
        (returns a complete string), internally we use a streaming request and
        collect chunks. This is critical for Cloudflare Tunnel users with
        verbose models like Qwen3.5: a non-streamed request makes the tunnel
        sit idle for 60-120 seconds waiting for the model to finish, which can
        cause buffer/timeout issues. A streamed request keeps bytes flowing
        throughout generation so the tunnel stays happy. Local IP users see
        no functional difference — just a different wire-protocol mode.
        """
        data_url = f"data:image/png;base64,{base64.b64encode(image_png).decode()}"
        raw_b64 = base64.b64encode(image_png).decode()
        sp = self._prepare_system_prompt(system_prompt)
        ut = self._prepare_user_text(user_text)

        messages_oai = [
            {"role": "system", "content": sp},
            {"role": "user", "content": [
                {"type": "text", "text": ut},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ]
        self._log_request(messages_oai, max_tokens, streaming=True, fmt="openai")
        try:
            return self._collect_stream(
                self._http_chat_openai(messages_oai, max_tokens, stream=True)
            )
        except RuntimeError as e:
            if not self._is_owui_image_error(e):
                self._log_response_body_on_error(e)
                raise
            print("[ScreenSense OWUI] OpenAI format rejected with image-preprocessor "
                  "error — retrying with Ollama-native format.",
                  file=__import__("sys").stderr, flush=True)

        # Fallback: Ollama-native format. Two key differences:
        #   1. content is a plain string (not a list of parts)
        #   2. images go in a separate field as raw base64 (no data: prefix)
        messages_ollama = [
            {"role": "system", "content": sp},
            {"role": "user", "content": ut, "images": [raw_b64]},
        ]
        self._log_request(messages_ollama, max_tokens, streaming=True, fmt="ollama")
        try:
            return self._collect_stream(
                self._http_chat_ollama(messages_ollama, max_tokens, stream=True),
                ollama_format=True,
            )
        except Exception as e:
            self._log_response_body_on_error(e)
            raise

    def _collect_stream(self, stream_iter, ollama_format: bool = False) -> str:
        """Consume a streaming generator and return the concatenated text.

        Also logs the cumulative size periodically so we can see in the log
        whether bytes are actually flowing through the tunnel or whether
        generation stalled out partway through.
        """
        import sys
        chunks: list[str] = []
        last_log_at = 0
        for chunk in stream_iter:
            chunks.append(chunk)
            total = sum(len(c) for c in chunks)
            # Log progress every ~2KB so the log isn't flooded
            if total - last_log_at > 2000:
                print(f"[ScreenSense OWUI] streaming progress: {total} chars",
                      file=sys.stderr, flush=True)
                last_log_at = total
        result = "".join(chunks)
        print(f"[ScreenSense OWUI] streaming complete: {len(result)} chars total",
              file=sys.stderr, flush=True)
        return result

    @staticmethod
    def _is_owui_image_error(exc: Exception) -> bool:
        """Detect the specific Open WebUI image-preprocessor errors.

        These trigger our auto-fallback to Ollama-native format.
        """
        msg = str(exc).lower()
        return (
            ("nonetype" in msg and "startswith" in msg)
            or ("'str' object has no attribute 'get'" in msg)
        )

    def _to_messages_ollama(self, conversation: Conversation, system_prompt: str) -> list[dict]:
        """Build messages in Ollama-native format (separate images field)."""
        out: list[dict] = [{"role": "system", "content": self._prepare_system_prompt(system_prompt)}]
        for t in conversation.turns:
            text = t.text or ""
            if t.role == "user":
                text = self._prepare_user_text(text or "What's on this screen?")
            msg: dict = {"role": t.role, "content": text}
            if t.role == "user" and t.image_png is not None:
                msg["images"] = [base64.b64encode(t.image_png).decode()]
            out.append(msg)
        return out

    def _http_chat_openai(self, messages, max_tokens, stream: bool):
        """POST to <base_url>/chat/completions (OpenAI-compatible endpoint)."""
        url = self.extra["base_url"].rstrip("/") + "/chat/completions"
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
            # Ollama-via-OpenAI accepts num_ctx through an "options" field
            # passed alongside the standard OpenAI parameters. Non-Ollama
            # OpenAI-compatible servers ignore unknown fields, so this is
            # safe to send everywhere. Same reason as in _http_chat_ollama —
            # we can't trust the Open WebUI UI to propagate num_ctx.
            "options": {
                "num_ctx": int(self.extra.get("num_ctx", 8192)),
            },
        }
        return self._http_post(url, body, stream=stream)

    def _http_chat_ollama(self, messages, max_tokens, stream: bool):
        """POST to Ollama's native /api/chat endpoint.

        Open WebUI proxies Ollama at /ollama/api/chat — we rewrite the URL
        from the OpenAI-compatible path to the native Ollama path.
        """
        base = self.extra["base_url"].rstrip("/")
        # base is currently like 'http://host:port/api' (OWUI OpenAI route).
        # The Ollama-native route on Open WebUI is at /ollama/api/chat.
        # Strip the trailing /api and add /ollama/api/chat.
        if base.endswith("/api"):
            host = base[: -len("/api")]
        elif base.endswith("/v1"):
            host = base[: -len("/v1")]
        else:
            host = base
        url = host + "/ollama/api/chat"
        body = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "num_predict": max_tokens,
                # CRITICAL: explicitly request a large context window. Ollama
                # defaults to 2048 tokens, which is too small for a system
                # prompt + image + answer space. Open WebUI's UI-level
                # num_ctx setting often fails to propagate to API requests,
                # so we set it here to guarantee it takes effect.
                # 8192 is a safe default — well above what a screenshot+
                # prompt needs, well below anything that would OOM the GPU
                # on a 9B-11B model. User can override via extras.
                "num_ctx": int(self.extra.get("num_ctx", 8192)),
            },
        }
        return self._http_post(url, body, stream=stream, ollama_format=True)

    def _http_post(self, url: str, body: dict, stream: bool, ollama_format: bool = False):
        """Shared HTTP POST helper. Returns either a string (non-streaming) or
        a generator of strings (streaming).

        Honors two optional self.extra fields for Cloudflare Tunnel users:
          - cf_access_client_id  + cf_access_client_secret: service-token
            headers for tunnels gated by Cloudflare Access policies.
          - tls_insecure: if True, disables certificate verification (useful
            for tunnels with self-signed origin certs; OFF by default).
        """
        import json as _json
        import ssl
        import urllib.request
        import urllib.error

        encoded = _json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json" if not stream else "text/event-stream",
            # Cloudflare's Browser Integrity Check (BIC) blocks requests with
            # automation-looking User-Agents (e.g. "Python-urllib/3.x") with
            # error 1010. Sending a current-Chrome UA tells BIC we're a
            # regular browser. This doesn't bypass Cloudflare Access — that's
            # still gated by the service token — it only stops BIC from
            # flagging us as a bot at the edge before requests reach Access.
            "User-Agent": _BROWSER_USER_AGENT,
        }

        # Cloudflare Access service token: gates that auto-redirect to an
        # identity provider login normally fail with our API client (we'd
        # receive HTML instead of JSON). The service token headers tell
        # Cloudflare to validate via API instead of redirecting.
        cf_id = self.extra.get("cf_access_client_id", "").strip()
        cf_secret = self.extra.get("cf_access_client_secret", "").strip()
        if cf_id and cf_secret:
            headers["CF-Access-Client-Id"] = cf_id
            headers["CF-Access-Client-Secret"] = cf_secret
            print(f"[ScreenSense OWUI] using Cloudflare Access service token "
                  f"(id={cf_id[:12]}…)",
                  file=__import__("sys").stderr, flush=True)

        req = urllib.request.Request(
            url,
            data=encoded,
            method="POST",
            headers=headers,
        )

        # Optional TLS-verify-skip for self-signed origin certs. The OpenAI
        # SDK doesn't expose this knob; with our urllib-based path we do.
        ctx = None
        if self.extra.get("tls_insecure", False):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            print("[ScreenSense OWUI] WARNING: TLS verification disabled.",
                  file=__import__("sys").stderr, flush=True)

        print(f"[ScreenSense OWUI] POST {url} "
              f"(payload {len(encoded)} bytes)",
              file=__import__("sys").stderr, flush=True)

        try:
            resp = urllib.request.urlopen(req, timeout=120, context=ctx)
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            # Detect Cloudflare Access login pages specifically. The check
            # is narrow — bare mention of "cloudflare" in a response body
            # isn't enough since Cloudflare-fronted servers can include CF
            # branding in non-Access error pages too.
            body_lower = body_text.lower()
            looks_like_cf_access = (
                "cloudflareaccess.com" in body_lower
                or "/cdn-cgi/access/" in body_lower
                or ("<title>" in body_lower
                    and "access" in body_lower
                    and "cloudflare" in body_lower)
            )
            if e.code in (302, 401, 403) and looks_like_cf_access:
                raise RuntimeError(
                    f"HTTP {e.code} from Cloudflare Access: this URL is "
                    f"protected by Cloudflare Access policies and your service "
                    f"token isn't being accepted. Check that your Access app "
                    f"has a policy with Action: Service Auth (not just Allow)."
                    f"\n\nResponse: {body_text[:500]}"
                ) from e
            raise RuntimeError(f"HTTP {e.code} from Open WebUI: {body_text}") from e
        except urllib.error.URLError as e:
            # SSL errors, connection refused, DNS failures
            reason = getattr(e, "reason", e)
            raise RuntimeError(
                f"Couldn't connect to {url}: {reason}"
            ) from e

        if not stream:
            data = _json.loads(resp.read().decode("utf-8"))
            try:
                if ollama_format:
                    # Ollama-native: {"message": {"content": "..."}}
                    msg = data.get("message") or {}
                    content = msg.get("content") or ""
                    # Thinking models (Qwen3.5, DeepSeek R1, etc.) put their
                    # reasoning in a separate "thinking" field. If content is
                    # empty but thinking has output, the model's actual answer
                    # is in there — extract it. The JSON parser further up the
                    # stack is tolerant of preambles, so embedded JSON in
                    # the thinking text will still parse correctly.
                    if not content.strip() and msg.get("thinking"):
                        thinking = msg["thinking"]
                        print(f"[ScreenSense OWUI] content empty but thinking "
                              f"field has {len(thinking)} chars — using that.",
                              file=__import__("sys").stderr, flush=True)
                        content = thinking
                else:
                    # OpenAI: {"choices": [{"message": {"content": "..."}}]}
                    msg = data["choices"][0]["message"]
                    content = msg.get("content") or ""
                    # OpenAI-compatible thinking models may expose reasoning
                    # under different keys depending on server implementation.
                    # Check the common ones.
                    if not content.strip():
                        for key in ("reasoning", "reasoning_content", "thinking"):
                            if msg.get(key):
                                print(f"[ScreenSense OWUI] content empty but "
                                      f"{key} field has output — using that.",
                                      file=__import__("sys").stderr, flush=True)
                                content = msg[key]
                                break
            except (KeyError, IndexError, TypeError) as e:
                raise RuntimeError(
                    f"Unexpected response shape: {data}"
                ) from e

            # Empty content is the silent failure mode we keep hitting with
            # local models — context overflow, num_predict=0, vision-encoder
            # failure, etc. Log enough metadata to actually diagnose it.
            if not content.strip():
                print(f"[ScreenSense OWUI] Empty response from server. "
                      f"Full server reply:\n{_json.dumps(data, indent=2)[:1500]}",
                      file=__import__("sys").stderr, flush=True)
                # Build a more useful error than "couldn't parse". Surface
                # any Ollama metadata that hints at the cause.
                hint = self._diagnose_empty_response(data, ollama_format)
                raise RuntimeError(
                    f"The model returned an empty response. {hint}"
                )
            return content

        # Streaming. For thinking models (Qwen3+, DeepSeek R1) the streamed
        # response splits into 'content' (final answer) and 'thinking' (the
        # chain-of-thought scratchpad). We yield content chunks normally;
        # if no content ever arrives, we accumulate thinking chunks and
        # yield those at the end as a fallback (matches the non-streaming
        # path's behavior).
        def _iter():
            thinking_buffer: list[str] = []
            saw_any_content = False
            for line in resp:
                if not line:
                    continue
                line = line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if ollama_format:
                    # Ollama-native streams plain JSON objects, one per line.
                    try:
                        chunk = _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
                    if chunk.get("done"):
                        break
                    msg = chunk.get("message", {}) or {}
                    delta = msg.get("content")
                    thinking_delta = msg.get("thinking")
                    if thinking_delta:
                        thinking_buffer.append(thinking_delta)
                else:
                    # OpenAI streams Server-Sent Events with "data: " prefix.
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(payload)
                    except _json.JSONDecodeError:
                        continue
                    try:
                        delta_obj = chunk["choices"][0]["delta"]
                    except (KeyError, IndexError, TypeError):
                        continue
                    delta = delta_obj.get("content")
                    # Some OpenAI-compatible thinking-model servers emit
                    # reasoning under different keys; capture the common ones.
                    for tk in ("reasoning", "reasoning_content", "thinking"):
                        if delta_obj.get(tk):
                            thinking_buffer.append(delta_obj[tk])
                if delta:
                    saw_any_content = True
                    yield delta
            # If the entire stream completed without any content chunks but
            # we captured thinking, surface that so the JSON parser at least
            # has something to work with.
            if not saw_any_content and thinking_buffer:
                joined = "".join(thinking_buffer)
                print(f"[ScreenSense OWUI] stream had no 'content' but "
                      f"{len(joined)} chars of 'thinking' — yielding that "
                      f"as fallback.", file=__import__("sys").stderr, flush=True)
                yield joined
        return _iter()

    @staticmethod
    def _diagnose_empty_response(data: dict, ollama_format: bool) -> str:
        """Inspect a server response dict to figure out why content is empty.

        Returns a human-readable hint with the most likely cause first.
        """
        # Ollama-native responses include a 'done_reason' field that's
        # extremely useful: 'length' = hit num_predict cap, 'stop' = natural
        # end (so empty means model emitted no tokens at all), 'load' =
        # model couldn't load, etc.
        done_reason = data.get("done_reason")
        finish_reason = None
        try:
            finish_reason = data["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError):
            pass

        reason = done_reason or finish_reason or ""

        if reason == "length":
            return ("Generation hit max_tokens before producing any text — "
                    "the input (system prompt + image) likely consumed the "
                    "entire context window. Try a smaller region capture, "
                    "or load the model in Ollama with a larger num_ctx "
                    "(`ollama run <model>` then `/set parameter num_ctx 8192`).")
        if reason == "load":
            return ("Model couldn't load — check that it's actually pulled "
                    "(`ollama pull <model>`) and that there's enough VRAM.")
        if reason in ("stop", ""):
            # No tokens emitted but generation stopped naturally. Typical
            # symptoms of vision encoder failure or num_predict=0.
            prompt_tokens = data.get("prompt_eval_count")
            eval_tokens = data.get("eval_count", 0)
            ctx_hint = ""
            if prompt_tokens:
                ctx_hint = (f" The model processed {prompt_tokens} input tokens "
                            f"and emitted {eval_tokens} output tokens.")
            return (f"The model accepted the request but produced no output."
                    f"{ctx_hint} Most likely the vision encoder failed on "
                    f"this image, or the context window is full. Try a "
                    f"different vision model, or capture a smaller region.")
        return f"(server reported reason: {reason!r})"

    def _log_request(self, messages, max_tokens, streaming: bool, fmt: str = "openai") -> None:
        """Log a redacted version of the request — base64 data replaced with
        a length marker so the log stays readable."""
        import sys
        import json as _json
        redacted = []
        for m in messages:
            content = m.get("content")
            # Ollama-format messages may have an "images" key — redact those too.
            redacted_msg = dict(m)
            if "images" in m and isinstance(m["images"], list):
                redacted_msg["images"] = [
                    f"<base64 image, {len(img)} chars>" if isinstance(img, str) else "<non-str>"
                    for img in m["images"]
                ]
            if isinstance(content, list):
                new_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url_field = part.get("image_url", "")
                        if isinstance(url_field, dict):
                            url = url_field.get("url", "")
                            marker = (f"<object form, base64 image, {len(url)} chars, "
                                      f"prefix={url[:30]!r}>")
                        elif isinstance(url_field, str):
                            marker = (f"<string form, base64 image, {len(url_field)} chars, "
                                      f"prefix={url_field[:30]!r}>")
                        else:
                            marker = f"<image_url type={type(url_field).__name__}>"
                        new_parts.append({"type": "image_url", "image_url": marker})
                    else:
                        new_parts.append(part)
                redacted_msg["content"] = new_parts
            redacted.append(redacted_msg)
        print(f"[ScreenSense OWUI] -> POST chat/completions "
              f"(format={fmt}, model={self.model}, max_tokens={max_tokens}, "
              f"num_ctx={self.extra.get('num_ctx', 8192)}, "
              f"streaming={streaming}):\n"
              f"{_json.dumps(redacted, indent=2)[:2000]}",
              file=sys.stderr, flush=True)

    def _log_response_body_on_error(self, exc: Exception) -> None:
        """Extract and log the raw HTTP response body from a failed request.

        The OpenAI SDK puts the response on the exception in various places
        depending on the error type. Try them all.
        """
        import sys
        body = None
        for attr in ("response", "body", "_response"):
            obj = getattr(exc, attr, None)
            if obj is None:
                continue
            # If it has a .text attribute, that's usually the raw body
            text = getattr(obj, "text", None)
            if isinstance(text, str) and text:
                body = text
                break
            # Some SDK versions stick the parsed dict on .body
            if isinstance(obj, dict):
                import json as _json
                body = _json.dumps(obj)
                break
        if not body:
            # Last resort — str(exc) often contains the body inline
            body = str(exc)
        print(f"[ScreenSense OWUI] <- ERROR response body:\n{body[:2000]}",
              file=sys.stderr, flush=True)


def list_openwebui_models(
    base_url: str,
    api_key: str,
    timeout: float = 10.0,
    cf_access_client_id: str = "",
    cf_access_client_secret: str = "",
    tls_insecure: bool = False,
) -> list[tuple[str, str]]:
    """Query an Open WebUI server for its available models.

    Returns a list of (model_id, display_label) pairs. On failure, returns
    a single-entry fallback so the user can still type a model ID manually.

    Supports Cloudflare Access service tokens for tunnels gated by Access
    policies, and optional TLS-verify-skip for self-signed origin certs.
    """
    import json as _json
    import ssl
    import urllib.request
    import urllib.error

    base_url = base_url.strip().rstrip("/")
    if not base_url.endswith("/v1") and not base_url.endswith("/api"):
        base_url = base_url + "/api"

    url = f"{base_url}/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        # Cloudflare BIC blocks Python's default urllib User-Agent — use a
        # browser-like one so the model-listing request gets through.
        "User-Agent": _BROWSER_USER_AGENT,
    }
    if cf_access_client_id and cf_access_client_secret:
        headers["CF-Access-Client-Id"] = cf_access_client_id
        headers["CF-Access-Client-Secret"] = cf_access_client_secret

    ctx = None
    if tls_insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_preview = ""
        try:
            body_preview = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        # Detect Cloudflare Access login redirect specifically. The check is
        # narrow on purpose — `body_preview` from any Cloudflare-fronted
        # error response can contain the word "cloudflare", so we need
        # stronger signals: the URL pattern Cloudflare uses for its login
        # pages, or its standard challenge HTML markers.
        looks_like_cf_access = (
            "cloudflareaccess.com" in body_preview.lower()
            or "/cdn-cgi/access/" in body_preview.lower()
            or ("<title>" in body_preview.lower()
                and "access" in body_preview.lower()
                and "cloudflare" in body_preview.lower())
        )
        if e.code in (302, 401, 403) and looks_like_cf_access:
            return [("__error__",
                     "Cloudflare Access blocked this URL — your service token "
                     "isn't being accepted. Check that your Access app has a "
                     "policy with Action: Service Auth.")]
        # Otherwise, include the raw response so the user/dev can see what
        # actually went wrong rather than getting a misleading guess.
        snippet = body_preview[:200] if body_preview else "(no body)"
        return [("__error__", f"HTTP {e.code} from server: {snippet}")]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return [("__error__", f"Couldn't reach server: {type(e).__name__}: {e}")]

    # Open WebUI follows OpenAI's /models shape: {"data": [{"id": "...", ...}, ...]}
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [("__error__", f"Unexpected response from {url}")]

    out: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("model")
        if not model_id:
            continue
        # Open WebUI sometimes includes a friendly name in 'name' or 'owned_by'.
        label = item.get("name") or model_id
        out.append((model_id, str(label)))
    if not out:
        return [("__error__", "Server returned no models")]
    return out


# ---- Provider registry ----

# For Open WebUI, the model list is fetched at runtime — we leave it as an
# empty list here. The setup dialog calls list_openwebui_models() to populate.
OPENWEBUI_MODELS: list[tuple[str, str]] = []

PROVIDERS = {
    "anthropic":  (AnthropicProvider,  ANTHROPIC_MODELS,  "claude-sonnet-4-6"),
    "openai":     (OpenAIProvider,     OPENAI_MODELS,     "gpt-5.4"),
    "google":     (GoogleProvider,     GOOGLE_MODELS,     "gemini-3-flash-preview"),
    "openwebui":  (OpenWebUIProvider,  OPENWEBUI_MODELS,  ""),  # no default — user picks
}


def humanize_provider_error(exc: Exception, provider_name: str, model: str) -> str:
    """Convert a vendor-SDK exception into a message a user can act on.

    Common cases: 404 (wrong/deprecated model name), 401/403 (bad/missing key),
    quota/rate-limit, network. Falls back to a clean type+message for unknowns.
    """
    msg = str(exc)
    lower = msg.lower()
    type_name = type(exc).__name__

    # Model not found — by far the most common confusing error after migration
    if "404" in msg or "not_found" in lower or "not found" in lower:
        return (
            f"The model '{model}' was not found on {provider_name}'s API.\n\n"
            f"This usually means the model name is wrong or has been deprecated. "
            f"Open the tray menu → AI Provider → and switch to a different model, "
            f"or edit screensense.json on your USB drive to change the model ID.\n\n"
            f"Original error: {type_name}: {msg[:200]}"
        )
    # Auth
    if "401" in msg or "403" in msg or "unauthorized" in lower or "invalid api key" in lower:
        return (
            f"{provider_name} rejected the API key (auth failed).\n\n"
            f"Make sure the key is correct and hasn't been revoked. You can "
            f"re-enter it via tray menu → AI Provider → Add / configure another "
            f"provider.\n\nOriginal error: {type_name}: {msg[:200]}"
        )
    # Quota / rate limit
    if "429" in msg or "rate" in lower and "limit" in lower or "quota" in lower:
        return (
            f"{provider_name} rate-limited or out of quota. Wait a minute and "
            f"try again, or switch to a different provider via the tray menu.\n\n"
            f"Original error: {type_name}: {msg[:200]}"
        )
    # Network
    if "connection" in lower or "timeout" in lower or "network" in lower:
        return (
            f"Network error talking to {provider_name}. Check your internet "
            f"connection.\n\nOriginal error: {type_name}: {msg[:200]}"
        )
    # Open WebUI / self-hosted: image rejected. The OWUI image preprocessor
    # throws these particular errors when it can't make sense of the image
    # field in our request. Since we now auto-fall-back to Ollama-native
    # format, if the user still sees this error it means BOTH formats failed.
    if ("nonetype" in lower and "startswith" in lower) or ("'str' object has no attribute 'get'" in lower):
        return (
            f"{provider_name} couldn't process the screenshot.\n\n"
            f"Both OpenAI Vision and Ollama-native image formats were rejected "
            f"by the server, which suggests either:\n"
            f"  • The model '{model}' isn't actually vision-capable (or the "
            f"vision weights aren't loaded — sometimes Ollama needs `ollama pull "
            f"{model}` to get the vision parts).\n"
            f"  • Your Open WebUI version has a bug in its image handling — "
            f"try updating it.\n"
            f"  • A reverse proxy is stripping the image data — try the local "
            f"IP directly if you've been using a tunnel/proxy.\n\n"
            f"You can switch to Claude, ChatGPT, or Gemini from the tray menu's "
            f"AI Provider submenu in the meantime.\n\n"
            f"Original error: {type_name}: {msg[:300]}"
        )
    # Self-hosted models occasionally return empty output. The provider raises
    # a RuntimeError with a useful diagnostic — pass it through verbatim
    # instead of wrapping it in the generic "TypeName: msg" format.
    if "empty response" in lower or "model returned an empty" in lower:
        return msg
    return f"{type_name}: {msg}"


def make_provider(provider_id: str, api_key: str, model: str, **extra) -> Provider:
    """Instantiate the provider class for the given ID.

    Extra kwargs are passed through to the provider's __init__ — used for
    things like base_url for self-hosted providers (Open WebUI / Ollama).
    """
    if provider_id not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider_id}")
    cls, _, _ = PROVIDERS[provider_id]
    return cls(api_key, model, **extra)
