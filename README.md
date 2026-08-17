# ScreenSense

A portable Windows app that watches your screen and answers questions about it using an LLM vision API. Lives on a USB stick — no install needed.

Considering Donating to help support with updates and new features!

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/B6U7259U1Y)

## What it does

- **Four AI providers supported**: Claude (Anthropic), ChatGPT (OpenAI), Gemini (Google), and self-hosted **Open WebUI** (works with any OpenAI-compatible server — Ollama, vLLM, LM Studio, etc.). Pick one in the installer, or configure multiple and switch on the fly from the tray menu. Each provider's API key is stored independently and the same passphrase unlocks all of them.

> **Self-hosted / Open WebUI:** point ScreenSense at your own server URL and pick from the models it has loaded (fetched live). Supports access through a **Cloudflare Tunnel**, including Cloudflare Access service-token auth and a Browser-Integrity-Check workaround. A thinking-capable vision model (e.g. `qwen3.6:27b` on a 24 GB GPU) gives strong accuracy on technical multiple-choice content while keeping screenshots entirely on your own hardware.

> **Note on Gemini model IDs:** Google's API model names include a `-preview` suffix for the 3.x family (`gemini-3-flash-preview`, `gemini-3.1-pro-preview`) — the marketing names "Gemini 3 Flash" don't work directly. The installer ships with the correct IDs. If you see a 404 error after a model is deprecated by Google, edit the `models.google` field in `screensense.json` on the USB drive to point at a current model ID from [https://ai.google.dev/gemini-api/docs/models](https://ai.google.dev/gemini-api/docs/models), or the stable `gemini-2.5-flash` (no preview required).

- **Always watching**: low-CPU background loop, perceptual-hash gate so it only reacts to real changes
- **Auto-suggest**: local OCR scans for error codes, tracebacks, and stack traces; if it sees something, a small toast appears offering to analyze
- **Ask anytime** (default `Ctrl+Shift+Space`): captures the screen and opens a chat panel — ask anything, follow up freely
- **Quiz mode** (default `Ctrl+Shift+Q`): finds multiple-choice questions on screen, identifies the correct answer(s), and draws high-contrast highlights directly over them. Handles single answers, "select N" questions, and "select all that apply". Designed with accessibility in mind — bold outline, optional screen dimming, pulse animation, large legible caption
- **Region modes** (default `Ctrl+Shift+R` ask / `Ctrl+Shift+Z` quiz): drag-select just part of the screen instead of capturing everything
- **Customizable hotkeys**: rebind all four capture actions from tray menu → Settings…
- **Telegram output**: optionally route answers to a Telegram chat instead of the screen — useful for second-screen / discreet workflows. When enabled, nothing appears on the main display (errors still show, so a misconfig is never silent)
- **Encrypted key**: optionally encrypts your API key with a passphrase so the USB stick is safe if lost
- **Drive wipe**: installer can quick-format the USB drive before deploying, with type-the-letter confirmation

## For end users: installing onto a USB drive

The easy path. You get one file — `ScreenSenseInstaller.exe` — and:

1. Plug in the USB drive you want to install onto.
2. Run `ScreenSenseInstaller.exe`.
3. The wizard walks you through: pick the drive, optionally wipe it clean first, choose your AI provider (Claude / ChatGPT / Gemini), paste that provider's API key (with optional passphrase encryption), choose options.
4. Hit Install. Takes about 10 seconds (or up to a minute if wiping).
5. Done. Plug the USB into any Windows PC and Windows offers to run ScreenSense from the AutoPlay popup.

Get an API key at https://console.anthropic.com/settings/keys.

**Safety:** The installer only ever shows removable drives — your C: drive never appears in the list. If you check "erase first," you'll be asked to type the drive letter to confirm before any data is touched. The drive's volume serial is captured at confirmation and re-checked immediately before formatting; if you've swapped sticks in the meantime, the operation aborts.

## For developers: building from source

### Architecture

```
Screen capture (mss, 2 FPS)
     │
     ▼  perceptual hash — skip if nothing changed
Change detector
     │
     ▼  OCR + regex — only on real changes, throttled to every 5s
Interest detector ─────► Toast notification (user opts in)
     │
     ▼  user hotkey OR toast click
LLM API — Claude, ChatGPT, or Gemini (streaming, vision)
     │
     ▼
Floating answer panel (with follow-up input)
```

The two local gates (hash + OCR) keep API costs sane. Without them, "always watching" would be roughly $5–15/hour. With them, idle cost is zero and active sessions cost cents per question.

### Project layout

```
screensense/
├── screensense/              ← the main app (what ends up on USB)
│   ├── app.py                ← controller wiring tray, hotkey, watcher, UI
│   ├── watcher.py            ← screen capture + change detection + OCR
│   ├── providers.py          ← LLM provider abstraction (Anthropic/OpenAI/Google/Open WebUI)
│   ├── llm_client.py         ← Qt threading layer around providers
│   ├── quiz.py               ← Quiz mode logic
│   ├── region_picker.py      ← Drag-to-select overlay
│   ├── highlight.py          ← Accessibility highlight overlay
│   ├── ui.py                 ← floating answer panel, toasts
│   ├── setup_dialog.py       ← first-run / add-provider dialog
│   ├── settings_dialog.py    ← hotkeys + Telegram output settings
│   ├── telegram.py           ← Telegram bot output helper
│   └── config.py             ← config load/save, passphrase encryption
├── installer/                ← the installer wizard
│   ├── wizard.py             ← QWizard pages
│   ├── drives.py             ← removable drive enumeration (ctypes)
│   ├── install_worker.py     ← file copy + config write
│   └── payload.py            ← locates bundled ScreenSense.exe
├── ScreenSense.spec          ← PyInstaller spec for main app
├── ScreenSenseInstaller.spec ← PyInstaller spec for installer
├── build_all.py              ← orchestrates both builds
├── autorun.inf               ← AutoPlay configuration
├── Install-Autorun.bat       ← optional true-autorun helper
└── requirements.txt
```

### Setup

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

### Run from source

```bash
python -m screensense    # main app (skips the installer)
python -m installer      # installer wizard
```

### Build for distribution

```bash
pip install pyinstaller
python build_all.py
```

This produces:
- `dist/ScreenSense.exe` — the portable app (~50 MB)
- `dist/ScreenSenseInstaller.exe` — the installer with `ScreenSense.exe` bundled inside (~70 MB)

Ship `ScreenSenseInstaller.exe`. That's the only file your users need.

## USB drive contents after install

```
USB_DRIVE:\
├── ScreenSense.exe              ← the app
├── autorun.inf                  ← drive icon + AutoPlay action label
├── Install-Autorun.bat          ← optional, registers true autorun (one-time admin)
└── screensense.json             ← config + API key (written by installer)
```

## About autorun

Windows has disabled true USB autorun since Windows 7 SP1 (security measure against autorun-worm malware). You have three options, in order of friction:

1. **Default (zero setup)**: User double-clicks `ScreenSense.exe` on the stick.
2. **AutoPlay popup (recommended)**: The installer writes `autorun.inf` to the stick, which registers ScreenSense as an AutoPlay action. When the stick is inserted, Windows shows a notification — one click runs the app. No admin rights needed.
3. **True autorun via scheduled task**: Check the box in the installer to register a scheduled task on the install PC that runs ScreenSense whenever USB volumes mount. Requires admin rights once (UAC prompt). The bundled `Install-Autorun.bat` does the same on other PCs later.

There is no way to get fully silent USB autorun without that one-time admin step on each machine — Microsoft removed the capability deliberately.

## Configuration

The config lives in `screensense.json` on the USB drive. It's multi-provider: each provider has its own key (plaintext or encrypted) and model, with a shared passphrase salt. Example (encrypted keys redacted):

```json
{
  "provider": "openwebui",
  "models": {
    "anthropic": "claude-sonnet-4-6",
    "openwebui": "qwen3.6:27b"
  },
  "keys_plaintext": {},
  "keys_encrypted": { "anthropic": "…", "openwebui": "…" },
  "salt_b64": "…",
  "extras": {
    "openwebui": {
      "base_url": "https://your-server.example.com",
      "cf_access_client_id": "…",
      "cf_access_client_secret": "…"
    }
  },
  "hotkey": "ctrl+shift+space",
  "quiz_hotkey": "ctrl+shift+q",
  "region_hotkey": "ctrl+shift+r",
  "region_quiz_hotkey": "ctrl+shift+z",
  "poll_fps": 2.0,
  "auto_suggest": true,
  "output_mode": "screen",
  "telegram_token": "",
  "telegram_chat_id": "",
  "highlight_dim_background": true,
  "highlight_pulse": true,
  "highlight_outline_width": 8,
  "highlight_color_rgb": [255, 230, 0]
}
```

Most settings (hotkeys, output mode, Telegram, provider switching) are editable in-app via the tray menu — you rarely need to hand-edit this file. **Never commit your real `screensense.json` to source control**; it contains API keys.

### Accessibility — quiz mode highlight

The highlight overlay is designed for users with visual impairments. Defaults are tuned to WCAG-level contrast:

- **Triple-layer outline** — thick black border + bright color middle + thin white inner. Visible on any background (light, dark, photo, gradient)
- **Bright yellow default** — high contrast against most screen content. Customize via `highlight_color_rgb` if yellow doesn't work for your vision
- **Screen dimming** — everything except the answer goes 55% darker, drawing the eye to the highlight. Disable with `highlight_dim_background: false`
- **Pulsing animation** — gentle 20 FPS pulse on outline width. Some users find motion distracting; disable with `highlight_pulse: false`
- **Large caption** — 18px bold sans-serif text above the box, with the question's correct answer and a confidence chip
- **Auto-dismiss** — overlay clears after 30 seconds, or Esc, or click anywhere

### Quiz mode — how it works

1. You see a question on screen and press `Ctrl+Shift+Q`
2. Local OCR scans the screen and captures every text region with its pixel coordinates
3. The screenshot is sent to Claude with a structured-output prompt asking it to identify the question type (single-answer, "select N", or "select all that apply") and return every correct answer with verbatim text
4. We match each answer's text against the OCR boxes to find pixel coordinates — independently, so a question with three correct answers gets three matches
5. The highlight overlay draws on top of every located answer, with numbered badges (1, 2, 3) when there are multiple. A single caption panel in a screen corner lists all correct answers so you can see them at a glance even if the highlights are spread across the screen

If OCR can't locate one of the answers (rare — happens with stylized text), the chat panel still shows the full answer; only the visual highlight is missing for that specific answer. Partial matches don't block the others.

**Supported question types:**
- **Standard MCQ** ("What is X?"): one highlight, no badge
- **Select N** ("Choose the two best…"): N highlights with numbered badges
- **Select all that apply**: every correct answer highlighted, badges numbered in screen order

## Cost estimate

Per request with a 1568px screenshot: ~1500 input tokens (image) + ~50 input tokens (prompt) + ~200 output tokens. Roughly $0.005–0.01 per question on Sonnet 4.5. The watcher itself costs nothing — only on-demand asks hit the API.

## Limitations

- Windows-focused. The Python code is cross-platform but the installer's drive detection and autorun are Windows-specific.
- Cannot capture content protected by DRM (Netflix, some banking apps) — Windows blocks screen capture of those windows.
- The API key on the USB stick is only as secure as your passphrase if encryption is enabled.
- **Self-hosted thinking models need to be large enough.** Small thinking models (≈9–13B) can reason endlessly without producing a final structured answer on hard questions. For quiz mode, use a ≥24–27B thinking model, or a non-thinking vision model for simpler tasks.
- **Telegram output** requires a bot token from @BotFather and the destination chat ID; the bot must have been messaged once before it can reply to you.

## Security

- **API keys and tokens never belong in the repo.** They live only in `screensense.json` on your USB stick (or your dev machine), which is gitignored. If you ever paste a key, Cloudflare service token, or Telegram bot token somewhere it could be exposed, rotate it.
- Keys can be encrypted at rest with a passphrase (PBKDF2 + Fernet). A single passphrase unlocks all configured providers.
- The `tls_insecure` Open WebUI option disables certificate verification — only use it on trusted networks (home LAN, Tailscale), never on public Wi-Fi.

## License

Released under the [MIT License](LICENSE). Note that ScreenSense depends on PySide6, which is LGPL — that's why it can be distributed as a portable binary without GPL obligations on this code. If you redistribute the built `.exe`, comply with the LGPL terms for the bundled Qt libraries.
