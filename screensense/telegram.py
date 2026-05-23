"""Telegram output helper.

Optionally routes ScreenSense answers to a Telegram chat instead of the
on-screen panel. Useful for second-screen / fact-checking workflows where
the answer should arrive on a phone without appearing on the (possibly
shared) main display.

Uses urllib rather than requests to avoid adding a dependency and to match
the rest of the codebase's direct-HTTP approach (see providers.py).

Setup, from the user's side:
  1. Message @BotFather on Telegram, create a bot, copy the token.
  2. Message the new bot once (so it's allowed to message you back).
  3. Get your chat id (e.g. via @userinfobot, or the getUpdates API).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

# Telegram caps messages at 4096 chars. Chunk a little under that to be safe.
_CHUNK = 4000
_TIMEOUT = 30


def send_to_telegram(token: str, chat_id: str, text: str) -> None:
    """Send text to a Telegram chat, chunked to fit Telegram's length cap.

    Raises RuntimeError with a human-readable message on failure so the
    caller can surface it on screen (Telegram-mode errors should NOT be
    silent — that's the one thing we always show on the display).
    """
    token = (token or "").strip()
    chat_id = (chat_id or "").strip()
    if not token:
        raise RuntimeError("No Telegram bot token configured.")
    if not chat_id:
        raise RuntimeError("No Telegram chat ID configured.")

    text = (text or "").strip() or "(empty response)"
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for start in range(0, len(text), _CHUNK):
        chunk = text[start:start + _CHUNK]
        body = json.dumps({
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                resp.read()  # drain
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                payload = json.loads(e.read().decode("utf-8", errors="replace"))
                detail = payload.get("description", "")
            except Exception:
                pass
            if e.code == 401:
                raise RuntimeError(
                    "Telegram rejected the bot token (401). Check the token "
                    "from @BotFather."
                ) from e
            if "chat not found" in detail.lower():
                raise RuntimeError(
                    "Telegram chat ID not found. Message your bot once first, "
                    "then get the chat ID and re-enter it."
                ) from e
            raise RuntimeError(f"Telegram error {e.code}: {detail or '(no detail)'}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"Couldn't reach Telegram: {type(e).__name__}: {e}") from e


def fetch_recent_chat_id(token: str) -> str:
    """Best-effort: pull the most recent chat id from getUpdates.

    Helps the user fill in the chat-id field. Returns the chat id as a string,
    or raises RuntimeError if none can be found (e.g. they haven't messaged
    the bot yet).
    """
    token = (token or "").strip()
    if not token:
        raise RuntimeError("Enter the bot token first.")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Bad bot token (401).") from e
        raise RuntimeError(f"Telegram error {e.code}.") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise RuntimeError(f"Couldn't reach Telegram: {type(e).__name__}: {e}") from e

    results = data.get("result") or []
    # Walk newest-first looking for any message with a chat id.
    for update in reversed(results):
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None:
            return str(cid)
    raise RuntimeError(
        "No recent messages found. Send any message to your bot first, "
        "then try again."
    )
