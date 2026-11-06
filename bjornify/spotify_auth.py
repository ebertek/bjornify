"""Headless Spotify OAuth and an explicit one-time authorization helper.

The Discord bot never prompts for browser input. Run this file interactively
inside the container to create or renew the six-month Spotify authorization.
"""

import getpass
import hmac
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

from spotipy.exceptions import SpotifyOauthError
from spotipy.oauth2 import CacheFileHandler, SpotifyOAuth

DEFAULT_REDIRECT_URI = "http://127.0.0.1:3000"
TOKEN_CACHE_PATH = Path("/app/secrets/spotipy_token.cache")
# Björnify controls playback; it does not need access to saved libraries.
# SpotifyOAuth accepts a list of scopes directly.
SCOPE = [
    "user-read-currently-playing",
    "user-read-playback-state",
    "user-modify-playback-state",
]
AUTHORIZE_COMMAND = "docker exec -it bjornify python /app/spotify_auth.py authorize"
REAUTH_MESSAGE = "Spotify authorization required; the bot owner must reconnect Spotify."


class SpotifyAuthorizationRequired(RuntimeError):
    """The saved authorization is missing, revoked, or expired."""


class HeadlessSpotifyOAuth(SpotifyOAuth):
    """Never try to read from a bot's non-interactive standard input."""

    def get_auth_response(self, open_browser=None):
        """Guide the owner to the explicit CLI instead of blocking."""
        raise SpotifyAuthorizationRequired(REAUTH_MESSAGE)

    def refresh_access_token(self, refresh_token):
        """Clear a cache invalidated by Spotify's six-month token lifetime."""
        try:
            return super().refresh_access_token(refresh_token)
        except SpotifyOauthError as error:
            if error.error != "invalid_grant":
                raise
            # Other OAuth errors may be temporary and must not destroy the cache.
            Path(self.cache_handler.cache_path).unlink(missing_ok=True)
            raise SpotifyAuthorizationRequired(REAUTH_MESSAGE) from None


def redirect_uri_from_environment():
    """Validate the browser redirect using Spotify's current URI policy."""
    uri = os.getenv("SPOTIPY_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    parsed = urlsplit(uri)
    has_invalid_parts = (
        parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    )
    is_loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "::1",
    }
    if (
        parsed.hostname == "localhost"
        or not parsed.netloc
        or has_invalid_parts
        or not (parsed.scheme == "https" or is_loopback_http)
    ):
        raise ValueError(
            "Invalid SPOTIPY_REDIRECT_URI. Register a matching HTTPS URL or "
            "http://127.0.0.1:3000 in the Spotify Developer Dashboard; "
            "'localhost' is no longer supported."
        )
    return uri


def make_auth_manager(state=None):
    """Share identical redirect, scope, and token cache for CLI and bot."""
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET are required.")
    return HeadlessSpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri_from_environment(),
        scope=SCOPE,
        state=state,
        open_browser=False,
        cache_handler=CacheFileHandler(cache_path=str(TOKEN_CACHE_PATH)),
    )


def authorize_interactively():
    """Exchange a manually copied redirect URL for a new cached token."""
    if not sys.stdin.isatty():
        raise ValueError("Run authorization from a terminal with docker exec -it.")
    state = secrets.token_urlsafe(32)
    manager = make_auth_manager(state=state)

    print("Open this Spotify authorization link in your browser:")
    print(manager.get_authorize_url())
    print()
    print(
        "After consenting, copy the FULL redirected URL from the address bar. "
        "With a loopback redirect, the browser may show 'connection refused'; "
        "the URL still contains the authorization code."
    )
    response_url = getpass.getpass("Paste redirected URL (hidden input): ").strip()

    received = urlsplit(response_url)
    expected = urlsplit(manager.redirect_uri)
    if (
        received.scheme != expected.scheme
        or received.netloc != expected.netloc
        or (received.path or "/") != (expected.path or "/")
    ):
        raise ValueError("Redirect URL does not match the registered callback.")

    returned_state, code = manager.parse_auth_response_url(response_url)
    if not returned_state or not hmac.compare_digest(state, returned_state):
        raise ValueError("Spotify OAuth state mismatch. Start authorization again.")
    if not code:
        raise ValueError("Spotify did not return an authorization code.")

    manager.get_access_token(code=code, check_cache=False, as_dict=False)
    TOKEN_CACHE_PATH.chmod(0o600)
    print(
        "Spotify authorization saved. Restart the Björnify container to "
        "reload the authorization if any commands still fail."
    )


def main():
    """Run only when explicitly invoked from a terminal by the owner."""
    if sys.argv[1:] != ["authorize"]:
        print(f"Usage: {AUTHORIZE_COMMAND}", file=sys.stderr)
        return 2
    # Prevent creation of a world-readable token file when a new cache is saved.
    os.umask(0o077)
    try:
        authorize_interactively()
    except SpotifyOauthError as error:
        print(
            f"Spotify authorization failed ({error.error or 'OAuth error'}).",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, SpotifyAuthorizationRequired) as error:
        print(f"Spotify authorization failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
