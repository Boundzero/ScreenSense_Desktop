"""Configuration management with multi-provider support.

The config file lives next to the executable on the USB stick.
API keys can be stored either:
  - plaintext (convenient, but readable by anyone with the stick)
  - encrypted with a user passphrase (prompted on app start)

The user can configure keys for any/all three providers (Anthropic, OpenAI,
Google) and switch the active provider at runtime via the tray menu.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def app_dir() -> Path:
    """Directory where the executable lives (USB root when frozen)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = app_dir() / "screensense.json"

# Provider IDs and their default models.
PROVIDER_IDS = ("anthropic", "openai", "google", "openwebui")


@dataclass
class Config:
    # Active provider — which one to actually use for API calls.
    provider: str = "anthropic"
    # Per-provider model selection. Falls back to a provider default if missing.
    models: dict = field(default_factory=dict)

    # Per-provider API keys. Either *_plaintext or *_encrypted is set; not both.
    # Stored as a dict so we can add providers without breaking existing configs.
    keys_plaintext: dict = field(default_factory=dict)    # {provider_id: api_key}
    keys_encrypted: dict = field(default_factory=dict)    # {provider_id: encrypted_b64}
    salt_b64: Optional[str] = None   # one salt shared across all encrypted keys

    # Per-provider extra config (base URLs for self-hosted servers, etc.).
    # Stored as {provider_id: {"base_url": "..."}}.
    extras: dict = field(default_factory=dict)

    # Hotkeys and watcher behavior
    hotkey: str = "ctrl+shift+space"
    quiz_hotkey: str = "ctrl+shift+q"
    region_hotkey: str = "ctrl+shift+r"
    region_quiz_hotkey: str = "ctrl+shift+z"
    poll_fps: float = 2.0
    auto_suggest: bool = True

    # Output routing. "screen" = normal on-screen panel/overlay.
    # "telegram" = send answers to a Telegram chat, suppress on-screen UI
    # (errors still show on screen so a misconfig isn't silent).
    output_mode: str = "screen"          # "screen" | "telegram"
    telegram_token: str = ""             # bot token from @BotFather
    telegram_chat_id: str = ""           # destination chat id

    # Highlight overlay accessibility settings
    highlight_dim_background: bool = True
    highlight_pulse: bool = True
    highlight_outline_width: int = 8
    highlight_color_rgb: tuple = (255, 230, 0)

    # ---- legacy / migration fields (kept so old configs still load) ----
    # If an older config is found with these single-provider fields, we
    # migrate them into the new multi-provider dicts during load().
    api_key_plaintext: Optional[str] = None
    api_key_encrypted: Optional[str] = None
    model: Optional[str] = None

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            data = json.loads(CONFIG_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return cls()
        # Only pass through fields we know about; ignore extras silently.
        known = {k: v for k, v in data.items() if k in cls.__annotations__}
        cfg = cls(**known)
        cfg._migrate_legacy()
        return cfg

    def _migrate_legacy(self) -> None:
        """Move old single-provider fields into the new multi-provider dicts."""
        if self.api_key_plaintext and "anthropic" not in self.keys_plaintext:
            self.keys_plaintext["anthropic"] = self.api_key_plaintext
        if self.api_key_encrypted and "anthropic" not in self.keys_encrypted:
            self.keys_encrypted["anthropic"] = self.api_key_encrypted
        if self.model and "anthropic" not in self.models:
            self.models["anthropic"] = self.model
        # Don't clear the old fields — we keep them around so older binaries
        # using this config still work. They'll just be ignored.

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2))

    def model_for(self, provider_id: str) -> Optional[str]:
        """Get the user's chosen model for a provider, or None if not set."""
        return self.models.get(provider_id)

    def has_key_for(self, provider_id: str) -> bool:
        """True if any key (plaintext or encrypted) is stored for the provider."""
        return (
            provider_id in self.keys_plaintext
            or provider_id in self.keys_encrypted
        )

    def configured_providers(self) -> list[str]:
        """List of provider IDs the user has set up keys for."""
        return [p for p in PROVIDER_IDS if self.has_key_for(p)]


# ---- Key encryption ----

def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))


def encrypt_api_key(api_key: str, passphrase: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """Returns (encrypted_b64, salt_b64). Pass an existing salt to reuse one across keys."""
    if salt is None:
        salt = os.urandom(16)
    key = _derive_key(passphrase, salt)
    token = Fernet(key).encrypt(api_key.encode())
    return token.decode(), base64.b64encode(salt).decode()


def decrypt_api_key(encrypted_b64: str, salt_b64: str, passphrase: str) -> str:
    salt = base64.b64decode(salt_b64)
    key = _derive_key(passphrase, salt)
    try:
        return Fernet(key).decrypt(encrypted_b64.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Wrong passphrase") from e
