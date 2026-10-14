"""Remote trigger HTTP server.

Serves a tiny mobile-friendly web page on the LAN with two buttons
(Ask Screen / Quiz Mode). Any device with the bookmarkable URL (which
includes a shared secret token) can tap a button to trigger ScreenSense
on the host computer. The answer always routes to Telegram so the host
screen stays silent regardless of the global output_mode setting.

Design notes:
    - stdlib-only HTTP server (http.server.ThreadingHTTPServer). No Flask
      or similar — one less dependency, pyinstaller-friendly.
    - All auth is via a shared token in the URL query string. Not strong
      security, but appropriate for home LAN use. See the help text in
      the settings dialog for scope.
    - The server runs on a background thread; triggers are dispatched by
      invoking the Qt signals the hotkey system already uses (safe
      because those signals are connected QueuedConnection and marshal
      to the main thread).
    - "Force Telegram" state is set on the ScreenSenseApp before firing
      the signal; the output path reads it to override output_mode for
      the duration of that one request, then clears it.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse


def generate_token() -> str:
    """Create a URL-safe secret token for the shared bookmark URL."""
    return secrets.token_urlsafe(16)


def detect_lan_ip() -> str:
    """Best-effort detection of this machine's primary LAN IP.

    Does NOT actually send a packet — just asks the OS which interface
    would be used to reach a public address. Returns 127.0.0.1 if the
    machine has no route to the internet (e.g. offline).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# The web page served to triggering devices. Mobile-first dark theme matching
# ScreenSense's panel aesthetic. The token is injected into button fetches.
# Keeping this inline avoids a static-files directory that PyInstaller would
# have to be told to bundle.
_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1E1F23">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="apple-touch-icon" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=">
<title>ScreenSense Remote</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    background: #1E1F23;
    color: #E6E6E6;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px;
    padding-top: calc(24px + env(safe-area-inset-top, 0px));
    padding-bottom: calc(24px + env(safe-area-inset-bottom, 0px));
    user-select: none;
    -webkit-user-select: none;
    -webkit-tap-highlight-color: transparent;
  }
  h1 {
    font-size: 20px;
    font-weight: 600;
    color: #A9B1BD;
    margin-bottom: 32px;
    text-align: center;
  }
  .buttons {
    width: 100%;
    max-width: 420px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  button {
    width: 100%;
    padding: 24px;
    font-size: 18px;
    font-weight: 600;
    color: #E6E6E6;
    background: #2A2C32;
    border: 1px solid #3A3D45;
    border-radius: 14px;
    cursor: pointer;
    transition: background 0.1s, transform 0.05s;
    -webkit-appearance: none;
    appearance: none;
  }
  button:active { transform: scale(0.98); background: #353841; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  button.primary { background: #4F6BED; border-color: #4F6BED; }
  button.primary:active { background: #6079F0; }
  .icon { font-size: 24px; margin-right: 8px; }
  .status {
    margin-top: 28px;
    min-height: 22px;
    font-size: 14px;
    color: #A9B1BD;
    text-align: center;
    transition: color 0.2s;
  }
  .status.ok { color: #56B25A; }
  .status.err { color: #E85C5C; }
  .status.working { color: #6B8AFF; }
</style>
</head>
<body>
  <h1>ScreenSense Remote</h1>
  <div class="buttons">
    <button id="ask">❓&nbsp;&nbsp;Ask Screen</button>
    <button id="quiz" class="primary">✅&nbsp;&nbsp;Quiz Mode</button>
  </div>
  <div class="status" id="status">Ready — answers will arrive in Telegram.</div>
<script>
(function() {
  var token = new URLSearchParams(window.location.search).get("t") || "";
  var statusEl = document.getElementById("status");
  var askBtn = document.getElementById("ask");
  var quizBtn = document.getElementById("quiz");

  function setStatus(msg, cls) {
    statusEl.textContent = msg;
    statusEl.className = "status" + (cls ? " " + cls : "");
  }

  function trigger(action) {
    askBtn.disabled = true;
    quizBtn.disabled = true;
    setStatus("Triggered — capturing…", "working");
    fetch("/trigger?action=" + encodeURIComponent(action) +
          "&t=" + encodeURIComponent(token), { method: "POST" })
      .then(function(r) { return r.json().catch(function() { return { ok: r.ok }; }); })
      .then(function(data) {
        if (data && data.ok) {
          setStatus("Sent — check Telegram for the answer.", "ok");
        } else {
          var err = (data && data.error) ? data.error : "Trigger failed.";
          setStatus(err, "err");
        }
      })
      .catch(function(e) { setStatus("Network error: " + e.message, "err"); })
      .finally(function() {
        setTimeout(function() {
          askBtn.disabled = false;
          quizBtn.disabled = false;
          setStatus("Ready — answers will arrive in Telegram.", "");
        }, 3000);
      });
  }

  askBtn.addEventListener("click", function() { trigger("ask"); });
  quizBtn.addEventListener("click", function() { trigger("quiz"); });
})();
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    # Set by TriggerServer before serving.
    expected_token: str = ""
    on_trigger: Optional[Callable[[str], tuple[bool, str]]] = None

    def log_message(self, format: str, *args) -> None:
        # Suppress default stderr access log — too noisy.
        pass

    def _check_token(self, params: dict) -> bool:
        got = (params.get("t", [""])[0] or "").strip()
        return bool(self.expected_token) and secrets.compare_digest(
            got, self.expected_token
        )

    def _json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            # Serve the page. Token check happens on /trigger, not here —
            # the page itself is harmless and loads with the token embedded.
            body = _PAGE_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/ping":
            # Health check for debugging. Does not require token.
            self._json(200, {"ok": True, "service": "screensense-trigger"})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path != "/trigger":
            self._json(404, {"ok": False, "error": "not found"})
            return
        if not self._check_token(params):
            self._json(401, {"ok": False, "error": "Bad or missing token."})
            return
        action = (params.get("action", [""])[0] or "").strip().lower()
        if action not in ("ask", "quiz"):
            self._json(400, {"ok": False, "error": "action must be 'ask' or 'quiz'."})
            return
        if self.on_trigger is None:
            self._json(500, {"ok": False, "error": "Server not wired to handler."})
            return
        try:
            ok, msg = self.on_trigger(action)
        except Exception as e:
            self._json(500, {"ok": False, "error": f"Internal error: {e}"})
            return
        if ok:
            self._json(200, {"ok": True})
        else:
            self._json(409, {"ok": False, "error": msg or "Trigger refused."})


class TriggerServer:
    """Runs the HTTP trigger server on a background thread.

    Caller provides an `on_trigger(action: str) -> (ok: bool, msg: str)`
    callback that fires the corresponding ScreenSense action and returns
    whether it was accepted. The callback is invoked on the HTTP worker
    thread; the ScreenSense side is responsible for marshaling back to the
    Qt main thread (we do this via QueuedConnection on the trigger signals).
    """

    def __init__(self, port: int, token: str, on_trigger: Callable[[str], tuple[bool, str]]):
        self._port = port
        self._token = token
        self._on_trigger = on_trigger
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        # Capture handler config via a tiny subclass per server so multiple
        # instances (if ever) don't clobber each other's state.
        token = self._token
        on_trigger = self._on_trigger

        class Handler(_Handler):
            pass
        Handler.expected_token = token
        Handler.on_trigger = staticmethod(on_trigger)

        # Bind to all interfaces so phones on the LAN can reach it.
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self._port), Handler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="ScreenSenseTriggerServer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        self._thread = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def token(self) -> str:
        return self._token

    def url(self, host: Optional[str] = None) -> str:
        """The bookmarkable URL. Host defaults to this machine's LAN IP."""
        if host is None:
            host = detect_lan_ip()
        return f"http://{host}:{self._port}/?t={self._token}"
