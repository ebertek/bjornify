"""Short-lived, owner-bound Spotify OAuth sessions and optional HTTPS callback.

An HTTPS reverse proxy forwards /spotify/callback to the internal HTTP server.
The Spotify authorization code and tokens never appear in Discord messages.
"""

import html
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from spotify_auth import make_auth_manager, redirect_uri_from_environment

_LOGGER = logging.getLogger(__name__)
AUTH_TIMEOUT_SECONDS = 600


@dataclass
class PendingAuthorization:
    """An unexpired, single-use OAuth authorization request."""

    owner_id: int
    manager: object
    expires_at: float


class SpotifyAuthorizations:
    """Keep one pending authorization per owner, protected across threads."""

    def __init__(self):
        self._pending = {}
        self._lock = threading.Lock()

    def begin(self, owner_id):
        """Create an authorization URL with an unpredictable, single-use state."""
        state = secrets.token_urlsafe(32)
        manager = make_auth_manager(state=state)
        with self._lock:
            self._pending = {
                key: value
                for key, value in self._pending.items()
                if value.owner_id != owner_id and value.expires_at > time.monotonic()
            }
            self._pending[state] = PendingAuthorization(
                owner_id, manager, time.monotonic() + AUTH_TIMEOUT_SECONDS
            )
        return manager.get_authorize_url()

    def finish(self, state, code=None, *, owner_id=None, error=None):
        """Validate state, ownership and lifetime, then exchange the code once."""
        if not state:
            raise ValueError(
                "Missing Spotify OAuth state. Start /spotify connect again."
            )
        with self._lock:
            pending = self._pending.get(state)
            if pending is None or pending.expires_at <= time.monotonic():
                self._pending.pop(state, None)
                raise ValueError("The Spotify login expired or was already used.")
            if owner_id is not None and pending.owner_id != owner_id:
                raise PermissionError("This authorization belongs to another user.")
            # Consume before exchange so callbacks cannot race or replay.
            del self._pending[state]

        if error:
            raise ValueError("Spotify login was cancelled or refused. Try again.")
        if not code:
            raise ValueError("Spotify did not supply an authorization code.")
        pending.manager.get_access_token(code=code, check_cache=False, as_dict=False)


def start_spotify_callback(authorizations):
    """Serve the exact HTTPS redirect path through an external TLS reverse proxy."""
    url = urlsplit(redirect_uri_from_environment())
    if url.scheme != "https":
        return None

    port = int(os.getenv("SPOTIFY_AUTH_CALLBACK_PORT", "8765"))
    if not 1 <= port <= 65535:
        raise ValueError("SPOTIFY_AUTH_CALLBACK_PORT must be in the range 1-65535.")
    expected_path = url.path or "/"

    class CallbackHandler(BaseHTTPRequestHandler):
        """Read only Spotify's one-time callback query parameters."""

        def log_message(self, _format, *args):  # pylint: disable=arguments-differ
            # Access logs must never print authorization codes or state values.
            _LOGGER.debug(
                "Spotify callback HTTP response %s", args[1] if len(args) > 1 else ""
            )

        def do_GET(self):  # pylint: disable=invalid-name
            """Handle a one-use OAuth callback without echoing the code."""
            parsed = urlsplit(self.path)
            if parsed.path != expected_path:
                self._send_page(404, "Unknown callback.")
                return
            params = parse_qs(parsed.query)
            if len(params.get("state", [])) != 1:
                self._send_page(
                    400, "Invalid Spotify login. Use /spotify connect again."
                )
                return
            state = params["state"][0]
            code = params.get("code", [None])[0]
            error = params.get("error", [None])[0]
            try:
                authorizations.finish(state, code, error=error)
            except (ValueError, PermissionError):
                self._send_page(
                    400, "Spotify login failed or expired. Use /spotify connect again."
                )
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Spotify authorization exchange failed")
                self._send_page(
                    502, "Spotify authorization failed. Use /spotify connect again."
                )
            else:
                self._send_page(200, "Spotify connected! Return to Discord.")

        def _send_page(self, status, message):
            body = (
                "<!doctype html><html lang='en'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Björnify Spotify</title><body><h1>Björnify</h1><p>"
                + html.escape(message)
                + "</p></body></html>"
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("0.0.0.0", port), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _LOGGER.info("Spotify OAuth callback listening internally on port %d", port)
    return server
