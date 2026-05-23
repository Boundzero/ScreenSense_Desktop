# ScreenSense — Project State

_Last updated: May 2026 (post-Qwen3.6 resolution)._

This document captures the full state of ScreenSense, the desktop screen-analysis app. It exists so future Claude conversations (or human collaborators) can pick up development without re-deriving context. Read it once before suggesting changes — many decisions look replaceable until you know why they're the way they are.

---

## What ScreenSense Does

ScreenSense is a portable Windows app (designed to run from a USB stick) that captures screen content and sends it to an LLM with vision capability to get answers. Use cases:

- Explaining error codes and tracebacks
- Summarizing charts and dashboards
- Answering multiple-choice quiz questions (the user is studying CCNA)
- General "what's on this screen and what do I do about it" questions

It's a tray-resident background app with global hotkeys, a polished floating answer panel, and an installer wizard that handles USB deployment including drive-wipe-before-install.

---

## Current Status

**Working:**
- All four providers operational: Anthropic Claude, OpenAI ChatGPT, Google Gemini, Open WebUI (self-hosted)
- Tray menu for switching providers at runtime
- Region capture + quiz mode (with DPI scaling fix)
- Full-screen quiz mode
- Auto-suggest watcher (detects error codes / quiz patterns and pops a toast)
- Highlight overlay (draws yellow outline around the correct answer on the user's actual screen)
- Cloudflare Tunnel support for Open WebUI with service token auth + Browser Integrity Check workaround
- Installer wizard with drive wipe, encrypted-or-plaintext API key storage, autorun.inf, optional UAC-confirmed scheduled-task autorun
- File logging via faulthandler so .exe crashes are diagnosable (no more silent vanish)
- **Streaming quiz mode** — quiz `complete_json()` requests `stream=True` internally and collects chunks. Keeps Cloudflare Tunnel happy on long generations and supports thinking models' `thinking` field as a content fallback
- **Verified end-to-end with `qwen3.6:27b` over Cloudflare tunnel** — local thinking model with vision capability, accurate on CCNA-grade technical content. Runs on a 24GB GPU (RTX 3090) at Q4 quantization
- **User-customizable hotkeys** — Settings dialog (tray menu → Settings…) lets the user rebind all four capture hotkeys (ask / quiz / region-ask / region-quiz) by pressing a key chord. Changes apply live via `HotkeyListener.rebind()` without restarting the app.
- **Telegram output mode** — answers can be routed to a Telegram chat instead of the screen. When enabled, NO on-screen UI appears (no panel, no thinking indicator, no highlight overlay, no auto-suggest toast) — only errors still surface on screen so a misconfig isn't silent. Configured via Settings dialog with token + chat-id fields, auto-detect chat id, and a test-message button.

**Known limitations / open issues:**
- OCR currently returns 0 boxes in most runs — needs investigation. Doesn't block functionality (worker proceeds without OCR boxes), but breaks the highlight-overlay feature
- **Thinking-model accuracy is size-dependent.** Qwen3.5:9b reasoned but never self-terminated on CCNA questions, producing 15K+ chars of thinking with no final output. Qwen3.6:27b doesn't have this issue — it both reasons AND finishes cleanly. Smaller models at the 9B-13B range are unreliable for this kind of task; larger thinking models (or non-thinking models for simpler tasks) work fine.
- Quiz mode for very long CCNA questions can hit max_tokens limit at 4000 on smaller local models — fix is to crop the region tighter, or use a model with more output capacity

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Tray icon + global hotkeys (Ctrl+Shift+Space/Q/R)         │
└────────────┬────────────────────────────────────────────────┘
             │
   ┌─────────▼──────────┐    ┌──────────────────┐
   │ Watcher thread     │    │ Region picker    │
   │ - mss screen grab  │    │ (transparent     │
   │ - perceptual hash  │    │  drag-rect       │
   │ - OCR for detect   │    │  overlay)        │
   └────────┬───────────┘    └──────┬───────────┘
            │ Frame                  │ (x,y,w,h)
            ▼                        ▼
   ┌──────────────────────────────────────┐
   │  ScreenSenseApp (controller)         │
   │  - routes user input to flows        │
   │  - manages QThread workers           │
   └──┬───────────────────────────────────┘
      │
   ┌──▼──────────┐      ┌──────────────┐    ┌──────────────────┐
   │ Quiz worker │ ───► │ Provider     │ ──►│ Claude/GPT/      │
   │ (JSON out)  │      │ abstraction  │    │ Gemini/OWUI APIs │
   └─────────────┘      │ (providers.  │    └──────────────────┘
   ┌─────────────┐      │   py)        │
   │ Chat worker │ ───► │              │
   │ (streaming) │      │              │
   └─────────────┘      └──────────────┘
      │
      ▼
   ┌──────────────────────────────────────┐
   │ AnswerPanel (UI) + HighlightOverlay  │
   └──────────────────────────────────────┘
```

The provider abstraction is the single biggest architectural win — adding a 4th provider was one subclass instead of 4 SDK integrations.

---

## File Structure

```
screensense/                              ← outer folder (where .venv lives)
├── PROJECT_STATE.md                       ← this file
├── README.md                              ← user-facing docs
├── requirements.txt
├── ScreenSense.spec                       ← PyInstaller spec for the app
├── ScreenSenseInstaller.spec              ← PyInstaller spec for the installer
├── build_all.py                           ← orchestrates both builds
├── run_screensense.py                     ← .exe entry point (file logging + faulthandler)
├── run_installer.py                       ← installer .exe entry point
├── autorun.inf                            ← USB AutoPlay hint
├── Install-Autorun.bat                    ← optional admin-side autorun setup
│
├── screensense/                           ← main app package
│   ├── __init__.py
│   ├── __main__.py                        ← `python -m screensense` entry
│   ├── app.py                             ← controller: tray, hotkeys, signal routing
│   ├── providers.py                       ← LLM provider abstraction (4 implementations)
│   ├── llm_client.py                      ← Qt threading layer around providers
│   ├── quiz.py                            ← Quiz mode worker + JSON parser
│   ├── config.py                          ← Multi-provider config with encryption
│   ├── setup_dialog.py                    ← First-run / "add provider" dialog
│   ├── settings_dialog.py                 ← Hotkeys + Telegram output settings
│   ├── telegram.py                        ← Telegram bot output helper (urllib)
│   ├── watcher.py                         ← Screen capture + change detection (background)
│   ├── ocr.py                             ← Thread-safe OCR helper (per-thread engines)
│   ├── region_picker.py                   ← Drag-to-select overlay with DPI handling
│   ├── highlight.py                       ← Accessibility highlight overlay
│   └── ui.py                              ← AnswerPanel, Toast, Card base classes
│
└── installer/                             ← installer wizard package
    ├── __init__.py
    ├── __main__.py
    ├── wizard.py                          ← 6-page QWizard UI
    ├── drives.py                          ← Removable-only drive enumeration (ctypes)
    ├── install_worker.py                  ← File copy + config write + drive polling
    ├── wipe.py                            ← Quick-format with type-letter confirmation
    └── payload.py                         ← Bundles the ScreenSense.exe into the installer
```

USB deployment after install:
```
D:\
├── ScreenSense.exe
├── screensense.json                       ← config
├── screensense.log                        ← runtime logs (created on first run)
├── diagnostics\
│   └── last_quiz_capture.png              ← latest quiz capture for debugging
└── autorun.inf
```

---

## Critical Architectural Decisions

These are the choices that took rounds of debugging to get right. **Don't revisit these without reading the reasoning** — they'll look replaceable but the alternatives have failed.

### 1. PySide6 over PyQt6 (LGPL licensing)
Allows portable distribution without GPL infection. PyQt6 would force the app to be GPL.

### 2. PyInstaller with selective excludes
`ScreenSense.spec` excludes only `tkinter`. Earlier versions excluded `unittest`, `pydoc`, `doctest`, `test` to slim the bundle — **every one of those was secretly imported by something in the OCR stack (rapidocr → onnxruntime → numpy → ...)**. Removing them caused silent `ModuleNotFoundError` failures in the watcher thread. **If you find yourself wanting to slim the .exe by excluding stdlib modules, don't.**

### 3. Provider abstraction (`providers.py`)
Single interface: `stream()` for chat, `complete_json()` for quiz. Each provider implements both. The interface is intentionally minimal — adding more (function calling, tool use, etc.) would explode the surface area.

The `Provider` base class accepts `**extra` kwargs to handle per-provider config (base_url for OWUI, future possibilities for other self-hosted providers).

### 4. OpenAI cloud and Open WebUI both use the same SDK… but different code paths
`OpenAIProvider` uses OpenAI's Responses API where available. `OpenWebUIProvider` subclasses it but sets `_use_responses_api = False` and bypasses the OpenAI SDK entirely in favor of direct `urllib` HTTP calls. **The reason**: the OpenAI SDK has historically reshaped image fields in ways that break Open WebUI compatibility. Direct HTTP = what we put in is what gets sent.

### 5. Open WebUI uses object-form image_url then falls back to Ollama-native
Open WebUI's image preprocessor rejects multiple OpenAI Vision formats with different "NoneType has no attribute X" errors. We try `{"image_url": {"url": "data:..."}}` first; if the server returns the telltale image-handler error, we automatically retry with Ollama-native format (`{"role": "user", "content": "text", "images": ["raw_base64"]}` to `/ollama/api/chat`). **The auto-fallback is essential** — different Open WebUI versions accept different formats.

### 6. num_ctx explicitly set in API requests, not in Open WebUI's UI
Open WebUI's UI-level `num_ctx` setting doesn't reliably propagate to API requests. We send `"options": {"num_ctx": 8192}` in every Ollama-format request body to guarantee it.

### 7. Browser-like User-Agent for Cloudflare Tunnel users
Cloudflare's Browser Integrity Check (BIC) returns Error 1010 (HTTP 403) when it sees the default Python-urllib User-Agent. We send a current-Chrome UA so BIC doesn't flag the request as automation. The actual access control is still the Cloudflare Access service token — UA spoofing doesn't bypass auth, just BIC's pre-auth bot check.

### 8. Cloudflare Access service tokens
For users tunneling through Cloudflare with Access policies. Setup dialog has an expandable "Behind a Cloudflare tunnel?" section that holds the CF-Access-Client-Id and CF-Access-Client-Secret. **On the Cloudflare side, the user must set a policy with Action: Service Auth — Allow policies still redirect to IdP login.**

### 9. Thread-safe OCR (`ocr.py`)
onnxruntime sessions are bound to the creating thread. Calling them cross-thread causes Windows access-violation crashes (silent process death, not a Python exception). Our `ocr.py` maintains a per-thread engine pool — first call from a given thread creates an engine, subsequent calls from the same thread reuse it. Worker threads (quiz mode) and the watcher thread each get their own engine.

### 10. QuizWorker does its own OCR inside the worker thread
Earlier the main thread did OCR before kicking off the worker. That triggered the cross-thread onnxruntime crash. Now the worker thread calls `ocr_image_with_positions()` itself, which auto-binds an engine to the worker thread.

### 11. Qt signal connections use `QueuedConnection` explicitly
Lambdas connected to signals have no QObject parent, so Qt's auto-detection runs the slot on the emitting (worker) thread instead of the main thread. Touching widgets from a non-main thread = hard Windows crash. **All worker→main connections must use bound methods AND specify `Qt.ConnectionType.QueuedConnection`**.

### 12. Region picker defers signal emission via `QTimer.singleShot(50, ...)`
Emitting `region_selected` synchronously from `mouseReleaseEvent` caused crashes when the receiver started Qt threads and modified widgets while Qt was still mid-event delivery. The 50ms timer also gives Windows GDI time to finish compositing after the picker hides, preventing mss back-to-back grab issues.

### 13. DPI scaling — multiply by `devicePixelRatio` everywhere
Qt reports logical pixels; mss takes physical pixels. On a 150% display (or 175%, which the dev has), they differ by 1.5x or 1.75x. RegionPicker multiplies emitted coords by DPR; HighlightOverlay divides incoming coords back by DPR. This was the cause of "the capture is grabbing the wrong area of the screen" symptoms.

### 14. file logging + faulthandler at the entry point
`run_screensense.py` tees stdout/stderr to `screensense.log` next to the executable. It also enables `faulthandler` so C-level crashes (onnxruntime, mss) produce a Python stack trace before the process dies. **This is non-negotiable for any PyInstaller-frozen app.** Without it, every crash is invisible and undebuggable.

### 14b. Streaming under the hood for OWUI quiz mode
`OpenWebUIProvider.complete_json()` keeps a non-streaming interface (returns a complete string) but internally requests `stream=True` and concatenates chunks via `_collect_stream()`. The reason: a non-streamed request makes Cloudflare Tunnel sit idle for 60-120 seconds while the model generates, which causes buffer/timeout issues. Streaming keeps bytes flowing throughout. The `_iter()` parser inside `_http_post()` also tracks the `thinking` field separately and yields it as a fallback if no content ever arrives — important for thinking models that put their entire response in `thinking` rather than `content`.

### 14c. Telegram output mode behavior contract
When `config.output_mode == "telegram"` (and token + chat-id are set), the app routes answers to Telegram and suppresses on-screen UI. The rules, which must be preserved if these paths are refactored:
- **Quiz results and chat answers** → sent to Telegram, NOT shown in the panel; the highlight overlay is skipped entirely.
- **Thinking/loading indicators** → replaced with a one-line "📸 captured — analyzing…" notice sent to Telegram (best-effort; notice failures are swallowed).
- **Errors** → ALWAYS shown on screen via `panel.show_error()`, never gated on mode. A silent misconfig is the worst outcome, so errors bypass Telegram routing.
- **If a Telegram send fails** → fall back to showing the answer (and the failure reason) on screen. Never lose the answer.
- **Auto-suggest** → suppressed entirely in Telegram mode. It's an on-screen toast interaction, and auto-firing to Telegram on every detected error would spam the chat.
- The gating helper is `_telegram_mode()`; delivery goes through `_send_telegram()` (errors fall back to screen) and `_telegram_notice()` (best-effort, swallows errors). Quiz text is built once by `_format_quiz_text()` and used by both screen and Telegram paths.

### 14d. Custom hotkeys via rebind
All four capture actions have user-editable hotkeys (`hotkey`, `quiz_hotkey`, `region_hotkey`, `region_quiz_hotkey`), edited through the Settings dialog. `HotkeyListener.rebind()` tears down and recreates the pynput `GlobalHotKeys` listener (its bindings are fixed at construction, and per pynput docs a stopped listener thread cannot be restarted — a fresh instance is always created). `stop()` joins the old listener thread (timeout 1s) so the OS keyboard hook is fully released before the new listener starts, avoiding a race that left new hotkeys dead.

The Settings dialog's `HotkeyEdit` widget captures a key chord and translates Qt key codes into the friendly token format the listener understands. Two non-obvious correctness points:
- **The base key is resolved from the Qt key code, NOT `event.text()`.** When a modifier like Ctrl is held, `event.text()` returns a control character (e.g. `\x01` for Ctrl+A), which fails `isprintable()` and silently drops the capture. Letters/digits are resolved from `Key_A..Key_Z` / `Key_0..Key_9` ranges directly. This was the bug that made hotkey editing appear broken.
- **The Meta/Windows key must map to `cmd`, not `win`** — pynput's `GlobalHotKeys` only recognizes `<cmd>`.

### 15. Encrypted API keys with shared passphrase
PBKDF2 + Fernet. Single passphrase unlocks all stored provider keys. Plaintext storage is an option for convenience. The config schema supports multi-provider key storage with backward-compatible migration from the old single-provider format.

### 16. Diagnostic image saved at `D:\diagnostics\last_quiz_capture.png`
Every quiz call writes the exact image bytes that were sent to the LLM. This is what lets the user verify the capture region after a "model didn't find a question" failure.

---

## Provider-Specific Quirks

### Anthropic
- Uses native image format `{"type": "image", "source": {"type": "base64", ...}}`
- `messages.stream()` for chat, `messages.create()` for quiz
- Best at strict JSON output — quiz mode is most reliable here

### OpenAI
- Tries Responses API (`responses.create()`) first, falls back to `chat.completions.create()`
- Uses Vision format `{"type": "input_image", "image_url": "data:..."}`
- Generally reliable; occasional wrapping of JSON in markdown fences (parser handles it)

### Google Gemini
- Uses `google-genai` SDK (the newer one) with legacy fallback to `google.generativeai`
- Model IDs require `-preview` suffix on 3.x family (`gemini-3-flash-preview`, `gemini-3.1-pro-preview`)
- Honors `response_mime_type: application/json` for reliable structured output
- Can be conservative about `found_question: true` even when there's clearly a question — prompt is aggressively permissive to compensate

### Open WebUI (self-hosted)
- **No model catalog** — fetched live via `list_openwebui_models()` from `/api/models`
- Two image formats supported with auto-fallback (OpenAI Vision → Ollama-native)
- num_ctx must be sent in request body, not relied on from UI setting
- Direct urllib HTTP, not OpenAI SDK
- Cloudflare Tunnel support via service token + browser User-Agent
- **Streaming under the hood for quiz mode.** `complete_json()` requests `stream=True` and assembles chunks via `_collect_stream()`. Keeps Cloudflare Tunnel happy and supports thinking models' streamed thinking field.
- **Thinking models** (Qwen3+, DeepSeek R1) emit reasoning in a separate `thinking` field — we fall back to reading that when `content` is empty. The streaming parser tracks both fields and yields thinking as a final fallback if no content ever arrives.
- `/no_think` directive added to system prompt + user message for known thinking models. Reliably honored by Qwen3.6:27b; ignored by Qwen3.5:9b (the 9B model can't self-terminate reasoning regardless of directives).
- **Recommended model for self-hosted vision + accuracy**: `qwen3.6:27b` (~17GB at Q4, needs 24GB GPU). Smaller thinking models (Qwen3.5:9b range) cannot reliably terminate their reasoning on hard structured-output tasks.

---

## Build Commands

```powershell
cd "C:\Users\Ross Fazio\Downloads\files\screensense"
.venv\Scripts\Activate.ps1

# Clean rebuild (do this whenever you change source — PyInstaller's
# cache has been confused by source changes before)
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Get-ChildItem -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# Build both
python build_all.py

# Deploy to USB
Copy-Item -Force dist\ScreenSense.exe D:\

# Verify before running on USB (ensures fresh binary):
Get-Item D:\ScreenSense.exe | Select-Object Name, LastWriteTime
```

If PyInstaller seems to be using stale code, double-check by importing the live source:

```powershell
python -c "from screensense.providers import OpenWebUIProvider; import inspect; print(inspect.getsource(OpenWebUIProvider._http_post)[:200])"
```

---

## Current Model IDs (May 2026)

These will go stale. Update them in `providers.py`:

- **Anthropic**: `claude-opus-4-7`, `claude-sonnet-4-6` (default), `claude-haiku-4-5`
- **OpenAI**: `gpt-5.5`, `gpt-5.4` (default), `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-4o`
- **Google**: `gemini-3-flash-preview` (default), `gemini-3.1-pro-preview`, `gemini-2.5-flash`, `gemini-2.5-pro`, `gemini-2.5-flash-lite`
- **Open WebUI**: fetched live; no hardcoded list

---

## Hotkeys

All four are user-editable via tray menu → Settings…. Defaults:

- `Ctrl+Shift+Space` — Ask about screen (full screen, chat mode with follow-up)
- `Ctrl+Shift+Q` — Quiz mode (find the correct answer, highlight on screen)
- `Ctrl+Shift+R` — Region picker → ask mode
- `Ctrl+Shift+Z` — Region picker → quiz mode

---

## Dev Environment

- Windows 11 (10.0.26200)
- Display scaling 1.75× confirmed via `devicePixelRatio` log
- Multi-monitor setup: virtualGeom QRect(0, -1080, 2878, 2314)
- Python 3.13 in the project's `.venv`
- USB drive at D:\ labeled SCREENSENSE
- Cloudflare Tunnel for remote Open WebUI access (hostname configured per-user in app settings)

---

## Open / Known Issues Worth Future Work

1. **OCR returning 0 boxes consistently.** Reported but never chased. Quiz mode still works (the worker proceeds without OCR boxes) but the highlight-overlay feature is broken because we don't know where on the captured image the answers are. Likely causes: rapidocr import path issue when PyInstaller-frozen, image preprocessing mismatch, or onnxruntime model not loading from the bundled location. Worth a focused debug session.

2. **Model size matters for thinking models on Open WebUI.** Resolved by upgrading from Qwen3.5:9b to Qwen3.6:27b. The 9B model could reason but never self-terminate on hard technical questions, producing endless thinking with no final output. The 27B model both reasons accurately AND finishes cleanly. Verified working over Cloudflare tunnel with consistent CCNA-accuracy. **If considering smaller thinking models in the future, expect this same pathology and use a non-thinking model instead (Llama 3.2 Vision, Gemma 3) for the 9-13B range.**

3. **There is no `tests/` directory.** Multiple bugs would have been caught by a simple import-smoke-test ("can we import every module and instantiate every Provider class"). This was flagged repeatedly but never built. Adding it would catch regressions like the stale `make_client` signature that took a round to debug.

4. **The `_http_chat_completions` dual return type is a code smell.** It returns either a string (non-streaming) or a generator (streaming) based on a parameter. Works but reads badly. Should split into two methods.

5. **Some installer copy still says "Anthropic API key"** in older error messages, despite the migration to multi-provider. Minor polish issue.

6. **Region picker `_active_picker` reference cleanup.** After the picker is dismissed via `deleteLater()`, `self._active_picker` becomes a dangling Python reference to a deleted C++ object. Nothing currently touches it, but if anything ever does, it'll throw "Internal C++ object already deleted." Should be set to None in the cleanup path.

---

## Pitfalls to Watch For

These are the bugs we hit. Each one wasted a debugging round.

- **PyInstaller stale builds.** When source changes don't seem to take effect, always do the aggressive cleanup: `Remove-Item -Recurse -Force build, dist` AND `Remove-Item __pycache__` recursively.
- **Frozen-binary exclude lists.** Don't exclude stdlib modules from the PyInstaller spec. They have non-obvious transitive imports.
- **Cross-thread Qt widget access.** ALWAYS use `Qt.ConnectionType.QueuedConnection` on worker→main signals. Never connect lambdas to worker signals.
- **mss + Qt DPI mismatch.** Always multiply Qt logical coords by `devicePixelRatio()` before passing to mss. Always divide back for rendering.
- **OpenWebUI image format.** Try OpenAI Vision format first, fall back to Ollama-native on the telltale errors. Don't assume one works.
- **Provider SDK reshaping.** The OpenAI SDK reshapes image fields in ways that break some self-hosted servers. Direct urllib HTTP for OWUI = predictable bytes on the wire.
- **Cloudflare Tunnel and User-Agent.** BIC blocks Python-urllib's default UA. Always send a browser-like UA from any client behind a CF tunnel.
- **Small thinking models can't self-terminate.** A 9B-13B thinking model can produce 15K+ chars of reasoning on a hard question and never reach a final answer state. If you need a thinking model for accuracy, you need at least 24-27B parameters. Below that range, prefer a non-thinking model and accept the accuracy tradeoff — don't try to fight a small thinking model into emitting structured output.

---

## When This Document is Out of Date

Update it whenever you:
- Add a provider, change a provider's quirks, or update model IDs
- Change a core architectural decision (anything in the "Critical Architectural Decisions" list)
- Discover a new pitfall
- Close out a known issue

Outdated docs are worse than no docs. If unsure whether a change matters, err toward updating.
