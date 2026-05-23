"""First-run / settings dialog for API key configuration.

Supports all three providers — user picks one, enters its key, optionally
encrypts with a passphrase. The user can return to this dialog later via
the tray menu to add additional providers or switch the active one.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QMessageBox, QVBoxLayout,
)

from .config import Config, encrypt_api_key, decrypt_api_key
from .providers import PROVIDERS
from .ui import STYLE


PROVIDER_INFO = {
    "anthropic": {
        "display": "Anthropic Claude",
        "key_url": "https://console.anthropic.com/settings/keys",
        "key_format": "sk-ant-…",
        "key_validator": lambda k: k.startswith("sk-ant-"),
        "needs_base_url": False,
    },
    "openai": {
        "display": "OpenAI ChatGPT",
        "key_url": "https://platform.openai.com/api-keys",
        "key_format": "sk-…  or  sk-proj-…",
        "key_validator": lambda k: k.startswith("sk-"),
        "needs_base_url": False,
    },
    "google": {
        "display": "Google Gemini",
        "key_url": "https://aistudio.google.com/app/apikey",
        "key_format": "AIza…   (or any Google AI Studio key)",
        "key_validator": lambda k: len(k) >= 20,
        "needs_base_url": False,
    },
    "openwebui": {
        "display": "Open WebUI (self-hosted)",
        "key_url": "Your Open WebUI account → Settings → Account → API Keys",
        "key_format": "API key from your Open WebUI server",
        "key_validator": lambda k: len(k) >= 8,
        "needs_base_url": True,
        "url_format": "http://192.168.1.50:3000  or  https://owui.example.com",
    },
}


class SetupDialog(QDialog):
    """Pick a provider, enter its API key, optionally encrypt with a passphrase."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ScreenSense — API setup")
        self.setStyleSheet(STYLE + "QDialog { background: #1E1F23; }"
                           "QComboBox { background: #2A2C32; border: 1px solid #3A3D45;"
                           " border-radius: 8px; padding: 8px 12px; }"
                           "QLabel { color: #E6E6E6; }")
        self.setMinimumWidth(480)

        self.result_provider: Optional[str] = None
        self.result_key: Optional[str] = None
        self.result_model: Optional[str] = None
        self.result_passphrase: Optional[str] = None
        self.result_base_url: Optional[str] = None
        self.result_cf_id: str = ""
        self.result_cf_secret: str = ""
        self.result_tls_insecure: bool = False

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Pick which AI provider you'd like to use. You can add more later "
            "from the tray menu."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        # Provider picker
        self.provider_combo = QComboBox()
        for pid in PROVIDERS.keys():
            self.provider_combo.addItem(PROVIDER_INFO[pid]["display"], pid)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_change)
        form.addRow("Provider:", self.provider_combo)

        # Server URL — only shown for self-hosted providers
        self.url_label = QLabel("Server URL:")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("http://192.168.1.50:3000")
        form.addRow(self.url_label, self.url_input)
        self.url_label.hide()
        self.url_input.hide()

        # API key field
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API key:", self.key_input)

        self.key_help = QLabel("")
        self.key_help.setOpenExternalLinks(True)
        self.key_help.setStyleSheet("color: #A9B1BD; font-size: 11px;")
        form.addRow("", self.key_help)

        # Model picker (filled in based on chosen provider)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(False)
        form.addRow("Model:", self.model_combo)

        # For Open WebUI: a button to fetch the model list from the server.
        # Hidden for cloud providers since their model lists are hardcoded.
        from PySide6.QtWidgets import QPushButton, QHBoxLayout, QWidget
        self.fetch_btn = QPushButton("Fetch models from server")
        self.fetch_btn.clicked.connect(self._fetch_openwebui_models)
        self.fetch_status = QLabel("")
        self.fetch_status.setStyleSheet("color: #A9B1BD; font-size: 11px;")
        fetch_row = QWidget()
        fr = QHBoxLayout(fetch_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(self.fetch_btn)
        fr.addWidget(self.fetch_status, 1)
        form.addRow("", fetch_row)
        fetch_row.hide()
        self._fetch_row = fetch_row

        # Cloudflare Access fields — only shown for Open WebUI, and even then
        # tucked behind a "Show advanced" toggle since most users don't need them.
        from PySide6.QtWidgets import QPushButton
        self.cf_toggle = QPushButton("▶ Behind a Cloudflare tunnel?")
        self.cf_toggle.setFlat(True)
        self.cf_toggle.setStyleSheet(
            "QPushButton { color: #6B8AFF; text-align: left; padding: 4px; "
            "border: none; } QPushButton:hover { color: #8AAAFF; }"
        )
        self.cf_toggle.clicked.connect(self._toggle_cf_section)
        form.addRow("", self.cf_toggle)
        self.cf_toggle.hide()
        self._cf_expanded = False

        self.cf_id_label = QLabel("CF Access Client ID:")
        self.cf_id_input = QLineEdit()
        self.cf_id_input.setPlaceholderText("xxxxxxxxxxxxxxxxxxxxxxxx.access")
        form.addRow(self.cf_id_label, self.cf_id_input)
        self.cf_id_label.hide()
        self.cf_id_input.hide()

        self.cf_secret_label = QLabel("CF Access Client Secret:")
        self.cf_secret_input = QLineEdit()
        self.cf_secret_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.cf_secret_input.setPlaceholderText("Long secret from Cloudflare dashboard")
        form.addRow(self.cf_secret_label, self.cf_secret_input)
        self.cf_secret_label.hide()
        self.cf_secret_input.hide()

        self.cf_insecure_chk = QCheckBox(
            "Skip TLS verification (only for self-signed origin certs)"
        )
        form.addRow("", self.cf_insecure_chk)
        self.cf_insecure_chk.hide()

        self.cf_help = QLabel(
            "If your Open WebUI is behind a Cloudflare Tunnel with Access policies, "
            "create a Service Token in the Cloudflare dashboard (Zero Trust → Access "
            "controls → Service Auth) and paste the values here. Set the policy "
            "action to <i>Service Auth</i> in the Cloudflare Access app config, "
            "otherwise requests will still be redirected to your IdP."
        )
        self.cf_help.setWordWrap(True)
        self.cf_help.setStyleSheet("color: #A9B1BD; font-size: 11px;")
        form.addRow("", self.cf_help)
        self.cf_help.hide()

        layout.addLayout(form)

        # Passphrase option
        self.encrypt_chk = QCheckBox(
            "Encrypt key(s) with a passphrase  (recommended for USB drives)"
        )
        layout.addWidget(self.encrypt_chk)

        pf = QFormLayout()
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_input.setPlaceholderText("Passphrase")
        self.pass_input.setEnabled(False)
        self.encrypt_chk.toggled.connect(self.pass_input.setEnabled)
        pf.addRow("Passphrase:", self.pass_input)
        layout.addLayout(pf)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Initialize with first provider
        self._on_provider_change(0)

    def _toggle_cf_section(self) -> None:
        """Expand or collapse the Cloudflare Access fields."""
        self._cf_expanded = not self._cf_expanded
        for w in (self.cf_id_label, self.cf_id_input,
                  self.cf_secret_label, self.cf_secret_input,
                  self.cf_insecure_chk, self.cf_help):
            w.setVisible(self._cf_expanded)
        self.cf_toggle.setText(
            ("▼" if self._cf_expanded else "▶") + " Behind a Cloudflare tunnel?"
        )

    def _on_provider_change(self, idx: int) -> None:
        pid = self.provider_combo.itemData(idx)
        info = PROVIDER_INFO[pid]
        _, models, default_model = PROVIDERS[pid]

        # Show/hide the URL row depending on the provider
        needs_url = info.get("needs_base_url", False)
        self.url_label.setVisible(needs_url)
        self.url_input.setVisible(needs_url)
        self._fetch_row.setVisible(needs_url)
        # The Cloudflare-tunnel section only appears for self-hosted providers.
        self.cf_toggle.setVisible(needs_url)
        if not needs_url:
            # Collapse it when switching away
            self._cf_expanded = False
            for w in (self.cf_id_label, self.cf_id_input,
                      self.cf_secret_label, self.cf_secret_input,
                      self.cf_insecure_chk, self.cf_help):
                w.hide()

        if needs_url:
            self.url_input.setPlaceholderText(info.get("url_format", ""))

        # Update model list
        self.model_combo.clear()
        if not models:
            # Open WebUI: no hardcoded catalog. User clicks "Fetch models"
            # after entering URL+key, or types one manually if the combo is
            # set editable.
            self.model_combo.setEditable(True)
            self.model_combo.lineEdit().setPlaceholderText(
                "Click 'Fetch models' after entering URL and key"
            )
        else:
            self.model_combo.setEditable(False)
            default_idx = 0
            for i, (model_id, label) in enumerate(models):
                self.model_combo.addItem(label, model_id)
                if model_id == default_model:
                    default_idx = i
            self.model_combo.setCurrentIndex(default_idx)

        # Update key field hint
        self.key_input.setPlaceholderText(info["key_format"])
        key_url = info["key_url"]
        if key_url.startswith("http"):
            self.key_help.setText(
                f"Get a key at <a href='{key_url}' style='color: #6B8AFF;'>"
                f"{key_url}</a>"
            )
        else:
            self.key_help.setText(key_url)

    def _fetch_openwebui_models(self) -> None:
        """Query the server for its model list and populate the dropdown."""
        from .providers import list_openwebui_models
        url = self.url_input.text().strip()
        key = self.key_input.text().strip()
        if not url:
            self.fetch_status.setText("Enter a server URL first.")
            return
        if not key:
            self.fetch_status.setText("Enter an API key first.")
            return
        self.fetch_status.setText("Fetching…")
        # Synchronous — the request is short and this is a one-time setup step.
        models = list_openwebui_models(
            url, key,
            cf_access_client_id=self.cf_id_input.text().strip(),
            cf_access_client_secret=self.cf_secret_input.text().strip(),
            tls_insecure=self.cf_insecure_chk.isChecked(),
        )
        # Detect the error sentinel from list_openwebui_models
        if len(models) == 1 and models[0][0] == "__error__":
            self.fetch_status.setText(models[0][1])
            self.fetch_status.setStyleSheet("color: #E85C5C; font-size: 11px;")
            # Auto-expand the Cloudflare section if it looks like an Access issue
            if "cloudflare" in models[0][1].lower() and not self._cf_expanded:
                self._toggle_cf_section()
            return

        self.model_combo.clear()
        self.model_combo.setEditable(False)
        for model_id, label in models:
            self.model_combo.addItem(label, model_id)
        self.model_combo.setCurrentIndex(0)
        self.fetch_status.setText(f"Found {len(models)} models.")
        self.fetch_status.setStyleSheet("color: #56B25A; font-size: 11px;")

    def _accept(self) -> None:
        pid = self.provider_combo.currentData()
        info = PROVIDER_INFO[pid]
        key = self.key_input.text().strip()
        if not key:
            QMessageBox.warning(self, "Missing API key", "Please enter an API key.")
            return
        if not info["key_validator"](key):
            ok = QMessageBox.question(
                self,
                "Unusual key format",
                f"This key doesn't look like a typical {info['display']} key "
                f"(expected: {info['key_format']}). Use it anyway?",
            )
            if ok != QMessageBox.StandardButton.Yes:
                return
        if self.encrypt_chk.isChecked() and not self.pass_input.text():
            QMessageBox.warning(
                self, "Passphrase required",
                "Enter a passphrase or uncheck encryption.",
            )
            return

        # Open WebUI requires both URL and key
        base_url = ""
        if info.get("needs_base_url"):
            base_url = self.url_input.text().strip()
            if not base_url:
                QMessageBox.warning(self, "Missing server URL",
                                    "Please enter the server URL.")
                return
            if not (base_url.startswith("http://") or base_url.startswith("https://")):
                QMessageBox.warning(self, "Invalid URL",
                                    "URL must start with http:// or https://")
                return

        self.result_provider = pid
        self.result_key = key
        # currentData() returns None when the combo is in editable mode and
        # the user typed a model name. Fall back to currentText() in that case.
        model = self.model_combo.currentData()
        if not model:
            model = self.model_combo.currentText().strip()
        self.result_model = model or None
        if not self.result_model:
            QMessageBox.warning(self, "Missing model",
                                "Please choose or type a model name.")
            return
        self.result_base_url = base_url if base_url else None
        # Cloudflare Access extras (only meaningful for self-hosted providers).
        self.result_cf_id = self.cf_id_input.text().strip() if info.get("needs_base_url") else ""
        self.result_cf_secret = self.cf_secret_input.text().strip() if info.get("needs_base_url") else ""
        self.result_tls_insecure = self.cf_insecure_chk.isChecked() if info.get("needs_base_url") else False
        self.result_passphrase = (
            self.pass_input.text() if self.encrypt_chk.isChecked() else None
        )
        self.accept()


class UnlockDialog(QDialog):
    """Prompts for passphrase to decrypt stored API keys."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ScreenSense — Unlock")
        self.setStyleSheet(STYLE + "QDialog { background: #1E1F23; }")
        self.setMinimumWidth(360)
        self.passphrase: Optional[str] = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Enter your passphrase to decrypt your API keys:"))
        self.input = QLineEdit()
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.input)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.input.text():
            return
        self.passphrase = self.input.text()
        self.accept()


def obtain_provider_and_key(config: Config) -> Optional[tuple[str, str, str, dict]]:
    """Returns (provider_id, api_key, model, extras) or None if user cancelled.

    extras is a dict of provider-specific config like {"base_url": "..."} —
    empty for providers that don't need it.

    Decrypts an existing config if needed, prompting for a passphrase. Runs
    the setup dialog if no provider is configured yet.
    """
    # Case 1: existing config with at least one provider set up
    if config.configured_providers():
        active = config.provider
        if active not in config.configured_providers():
            active = config.configured_providers()[0]

        model = config.model_for(active) or PROVIDERS[active][2]
        extras = config.extras.get(active, {})

        # Plaintext key — easy path
        if active in config.keys_plaintext:
            return active, config.keys_plaintext[active], model, extras

        # Encrypted key — need the passphrase
        if active in config.keys_encrypted and config.salt_b64:
            while True:
                dlg = UnlockDialog()
                if dlg.exec() != QDialog.DialogCode.Accepted:
                    return None
                try:
                    key = decrypt_api_key(
                        config.keys_encrypted[active],
                        config.salt_b64,
                        dlg.passphrase,
                    )
                    return active, key, model, extras
                except ValueError:
                    QMessageBox.warning(None, "Wrong passphrase", "Try again.")

    # Case 2: first run — show the setup dialog
    dlg = SetupDialog()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None

    pid = dlg.result_provider
    key = dlg.result_key
    model = dlg.result_model
    extras: dict = {}
    if dlg.result_base_url:
        extras["base_url"] = dlg.result_base_url
    if dlg.result_cf_id and dlg.result_cf_secret:
        extras["cf_access_client_id"] = dlg.result_cf_id
        extras["cf_access_client_secret"] = dlg.result_cf_secret
    if dlg.result_tls_insecure:
        extras["tls_insecure"] = True

    if dlg.result_passphrase:
        enc, salt = encrypt_api_key(key, dlg.result_passphrase)
        config.keys_encrypted[pid] = enc
        config.salt_b64 = salt
    else:
        config.keys_plaintext[pid] = key
    config.models[pid] = model
    if extras:
        config.extras[pid] = extras
    config.provider = pid
    config.save()

    return pid, key, model, extras


def add_provider_dialog(config: Config, existing_passphrase: Optional[str]) -> bool:
    """Show the setup dialog to add ANOTHER provider to an existing config.

    Reuses the existing passphrase if keys are encrypted (we'd otherwise need
    to re-prompt). Returns True if a new provider was added.
    """
    dlg = SetupDialog()
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False

    pid = dlg.result_provider
    key = dlg.result_key
    model = dlg.result_model

    if dlg.result_passphrase:
        from base64 import b64decode
        existing_salt = (
            b64decode(config.salt_b64) if config.salt_b64 else None
        )
        enc, salt = encrypt_api_key(key, dlg.result_passphrase, salt=existing_salt)
        config.keys_encrypted[pid] = enc
        if not config.salt_b64:
            config.salt_b64 = salt
    else:
        config.keys_plaintext[pid] = key
    config.models[pid] = model
    # Build and persist extras dict (base URL plus Cloudflare Access if any).
    extras: dict = {}
    if dlg.result_base_url:
        extras["base_url"] = dlg.result_base_url
    if dlg.result_cf_id and dlg.result_cf_secret:
        extras["cf_access_client_id"] = dlg.result_cf_id
        extras["cf_access_client_secret"] = dlg.result_cf_secret
    if dlg.result_tls_insecure:
        extras["tls_insecure"] = True
    if extras:
        config.extras[pid] = extras
    config.save()
    return True
