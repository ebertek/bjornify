"""Standalone read-only Spotify Connect OAuth experiment (not part of Björnify runtime).

Run inside the already-running Björnify container with the existing Spotify
client environment. The probe caches its broader OAuth grant under /tmp only,
never touches the production Spotipy cache, and never sends playback commands.
"""

import getpass
import hmac
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

# The probe is copied into /tmp in the running container; the actual OAuth
# implementation is in /app. Do not import the bot entrypoint.
sys.path.insert(0, "/app")

from spotify_auth import (  # noqa: E402  # pylint: disable=wrong-import-position
    HeadlessSpotifyOAuth,
    PrivateTokenCache,
    SCOPE,
    redirect_uri_from_environment,
)

CACHE_PATH = Path("/tmp/bjornify-connect-probe.cache")
PROBE_SCOPES = [*SCOPE, "streaming"]


def make_probe_manager(*, state=None):
    """Use the same personal Spotify app but a separate disposable cache."""
    client_id = os.environ["SPOTIPY_CLIENT_ID"]
    client_secret = os.environ["SPOTIPY_CLIENT_SECRET"]
    return HeadlessSpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri_from_environment(),
        scope=PROBE_SCOPES,
        state=state,
        open_browser=False,
        cache_handler=PrivateTokenCache(cache_path=str(CACHE_PATH)),
    )


def read_token(manager):
    """Refresh if necessary, and verify the Spotify streaming permission."""
    token = manager.get_access_token(as_dict=True)
    granted = token.get("scope", "")
    granted_scopes = set(granted.split() if isinstance(granted, str) else granted)
    if "streaming" not in granted_scopes:
        raise ValueError(
            "OAuth returned no 'streaming' scope. The experimental session "
            "cannot be tested with this grant."
        )
    return token


def authorize():
    """Explicit one-time interactive grant; never expose the code in logs."""
    if not sys.stdin.isatty():
        raise ValueError("Run 'authorize' interactively with docker exec -it.")
    state = secrets.token_urlsafe(32)
    manager = make_probe_manager(state=state)
    print("OPEN this link to grant streaming to the SAME Spotify application:")
    print(manager.get_authorize_url())
    print()
    print(
        "Spotify may redirect to your running Björnify callback and show a "
        "400 error: that is expected because this probe uses its own OAuth "
        "state. Copy the FULL final URL from the browser address bar."
    )
    response = getpass.getpass("Paste redirect URL (hidden input): ").strip()
    actual = urlsplit(response)
    expected = urlsplit(manager.redirect_uri)
    if (
        actual.scheme != expected.scheme
        or actual.netloc != expected.netloc
        or (actual.path or "/") != (expected.path or "/")
    ):
        raise ValueError("Redirect URL does not match the configured callback.")
    returned_state, code = manager.parse_auth_response_url(response)
    if not returned_state or not hmac.compare_digest(state, returned_state):
        raise ValueError("OAuth state mismatch; no token was exchanged.")
    if not code:
        raise ValueError("No OAuth authorization code returned.")
    manager.get_access_token(code=code, check_cache=False, as_dict=True)
    token = read_token(manager)
    CACHE_PATH.chmod(0o600)
    print("Granted scopes:", " ".join(sorted(token["scope"].split())))
    print("PASS: isolated streaming grant saved; production token unchanged.")


def status():
    """Print only scope names and cache availability, never token contents."""
    if not CACHE_PATH.exists():
        print("No experimental OAuth token saved. Run 'authorize' first.")
        return 1
    cached = make_probe_manager().cache_handler.get_cached_token()
    if not cached:
        print("Experimental token missing or unreadable.")
        return 1
    scope = cached.get("scope", "")
    scopes = scope.split() if isinstance(scope, str) else (scope or [])
    print("Experimental cache present. Granted scopes:", " ".join(sorted(scopes)))
    print("Production OAuth cache remains separate.")
    return 0


def pipe_token():
    """Write a token only into a pipe, for the dedicated read-only Rust probe."""
    if sys.stdout.isatty():
        raise ValueError("Refusing to print a Spotify token to the terminal.")
    token = read_token(make_probe_manager())["access_token"]
    sys.stdout.write(token)
    sys.stdout.flush()


def main():
    """Dispatch only the explicit experimental operations."""
    if len(sys.argv) != 2 or sys.argv[1] not in (
        "authorize", "status", "pipe-token"
    ):
        print("Usage: oauth_probe.py authorize|status|pipe-token", file=sys.stderr)
        return 2
    os.umask(0o077)
    try:
        operation = sys.argv[1]
        if operation == "authorize":
            authorize()
        elif operation == "status":
            return status()
        else:
            pipe_token()
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        # Never include tokens or callback query parameters in the error.
        print(
            "Probe failed: " + type(error).__name__
            + ". See the documented setup, scopes and callback instructions.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
