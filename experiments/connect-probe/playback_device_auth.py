"""Experimental Spotify Connect device-code grant, isolated from Björnify OAuth.

This helper never starts playback. It is explicitly invoked, and saves a
streaming-only Spotify desktop-client grant in /app/secrets (persistent volume).
Never copy the cache into a repository or paste tokens into chat.
"""

import importlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import requests

# The Spotify desktop client identity used by librespot's Linux SessionConfig.
# Spotify currently enables the OAuth device-code flow for this client.
PLAYBACK_CLIENT_ID = "65b708073fc0480ea92a077233ca87bd"
DEVICE_AUTHORIZE_URL = "https://accounts.spotify.com/oauth2/device/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
CACHE_PATH = Path(os.environ.get("BJORNIFY_CONNECT_TOKEN_CACHE_PATH", "/app/secrets/spotify_connect_playback.cache"))
REQUEST_TIMEOUT = (4, 12)
MAX_RESPONSE_BYTES = 16 * 1024
REFRESH_MARGIN_SECONDS = 90
MAX_PAIRING_SECONDS = 600
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
KNOWN_ERRORS = {
    "authorization_pending",
    "slow_down",
    "access_denied",
    "expired_token",
    "invalid_grant",
    "invalid_scope",
    "invalid_client",
    "unauthorized_client",
}


def spotify_post(url, fields):
    """Submit only form fields to fixed Spotify OAuth endpoints."""
    if url not in (DEVICE_AUTHORIZE_URL, TOKEN_URL):
        raise ValueError("Unexpected OAuth endpoint.")
    response = requests.post(
        url,
        data=fields,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=False,
    )
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError("OAuth response exceeded the configured size limit.")
    if response.status_code not in (200, 400):
        raise ValueError("Unexpected OAuth HTTP status.")
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("Unexpected OAuth response format.")
    if response.status_code != 200:
        reason = body.get("error")
        if reason not in KNOWN_ERRORS:
            raise ValueError("Spotify denied the OAuth request.")
        return {"error": reason}
    return body


def save_grant(payload, *, old_refresh_token=None):
    """Atomically save a streaming grant without disclosing credentials."""
    access = payload.get("access_token")
    refresh = payload.get("refresh_token") or old_refresh_token
    scopes = payload.get("scope", "streaming")
    granted = scopes.split() if isinstance(scopes, str) else []
    lifetime = payload.get("expires_in", 0)
    if not isinstance(access, str) or not access:
        raise ValueError("No access token in OAuth reply.")
    if not isinstance(refresh, str) or not refresh:
        raise ValueError("No refresh token in OAuth reply.")
    if "streaming" not in granted or not isinstance(lifetime, int) or lifetime <= 0:
        raise ValueError("OAuth grant lacks required streaming permission or expiry.")
    record = {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": time.time() + lifetime,
        "scope": " ".join(granted),
    }
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=CACHE_PATH.parent,
        prefix=".bjornify-connect-playback-",
        delete=False,
    ) as file:
        temp_name = file.name
        try:
            os.chmod(temp_name, 0o600)
            json.dump(record, file)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temp_name, CACHE_PATH)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return record


def read_grant():
    """Load only from the isolated experiment cache."""
    if not CACHE_PATH.is_file():
        raise ValueError("No experimental playback grant; run authorize.")
    with CACHE_PATH.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise TypeError("Invalid experimental playback grant.")
    return payload


def valid_grant():
    """Refresh the temporary access token when close to expiration."""
    data = read_grant()
    if (
        isinstance(data.get("expires_at"), (int, float))
        and data["expires_at"] > time.time() + REFRESH_MARGIN_SECONDS
    ):
        return data
    refreshed = spotify_post(
        TOKEN_URL,
        {
            "client_id": PLAYBACK_CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
        },
    )
    if "error" in refreshed:
        raise ValueError("Playback OAuth refresh refused: " + refreshed["error"])
    return save_grant(refreshed, old_refresh_token=data["refresh_token"])


def authorize():
    """Pair this experiment through Spotify, not Björnify's callback URL."""
    if not sys.stdin.isatty():
        raise ValueError("Run authorize interactively with docker exec -it.")
    result = spotify_post(
        DEVICE_AUTHORIZE_URL,
        {"client_id": PLAYBACK_CLIENT_ID, "scope": "streaming"},
    )
    if "error" in result:
        raise ValueError("Playback pairing unavailable: " + result["error"])
    device_code = result.get("device_code")
    user_code = result.get("user_code")
    lifetime = result.get("expires_in")
    interval = result.get("interval", 5)
    code_ok = isinstance(device_code, str) and bool(device_code)
    user_ok = isinstance(user_code, str) and user_code.isalnum()
    expiry_ok = isinstance(lifetime, int) and lifetime > 0
    interval_ok = isinstance(interval, int) and interval > 0
    if not (code_ok and user_ok and expiry_ok and interval_ok):
        raise ValueError("Spotify returned an invalid pairing challenge.")

    print("Open https://spotify.com/pair and enter this temporary code:")
    print(user_code)
    print("Approve Spotify playback authentication; Björnify production is unchanged.")
    deadline = time.monotonic() + min(lifetime, MAX_PAIRING_SECONDS)
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        result = spotify_post(
            TOKEN_URL,
            {
                "client_id": PLAYBACK_CLIENT_ID,
                "grant_type": GRANT_TYPE,
                "device_code": device_code,
            },
        )
        if "error" not in result:
            save_grant(result)
            print("PASS: isolated playback grant saved. No playback commands sent.")
            return
        if result["error"] == "authorization_pending":
            continue
        if result["error"] == "slow_down":
            interval += 5
            continue
        raise ValueError("Playback pairing failed: " + result["error"])
    raise ValueError("Playback pairing expired; start authorize again.")


def status():
    """Print cache status without exposing access or refresh tokens."""
    try:
        grant = read_grant()
    except ValueError:
        print("No saved experimental playback grant.")
        return 1
    valid_scope = "streaming" in grant.get("scope", "").split()
    not_expired = grant.get("expires_at", 0) > time.time()
    print(
        "Playback grant: cached,",
        "streaming=" + str(valid_scope).lower() + ",",
        "access_token_current=" + str(not_expired).lower(),
    )
    print("READ ONLY: production Björnify OAuth cache is separate.")
    return 0


def pipe_token():
    """Write an access token only to a pipe, never a terminal or log."""
    if sys.stdout.isatty():
        raise ValueError("Refusing to print a Spotify access token to the terminal.")
    grant = valid_grant()
    sys.stdout.write(grant["access_token"])
    sys.stdout.flush()


def dual_tokens():
    """Pass separate Connect and personal Web API tokens through one private pipe.

    Reads the production Web API token cache without changing or refreshing it.
    The output must be consumed only by the dedicated Rust experimental probe.
    """
    if sys.stdout.isatty():
        raise ValueError("Refusing to print Spotify access tokens to the terminal.")
    personal_manager = importlib.import_module("spotify_auth").make_auth_manager()
    cached = personal_manager.cache_handler.get_cached_token()
    if (
        not isinstance(cached, dict)
        or not isinstance(cached.get("access_token"), str)
        or cached.get("expires_at", 0) <= time.time() + REFRESH_MARGIN_SECONDS
        or "user-read-playback-state" not in cached.get("scope", "").split()
    ):
        raise ValueError(
            "Personal Web API cache unavailable or expiring; let production Björnify refresh it."
        )
    playback = valid_grant()
    sys.stdout.write(
        json.dumps(
            {
                "playback_token": playback["access_token"],
                "web_api_token": cached["access_token"],
            }
        )
    )
    sys.stdout.flush()


def print_client_id():
    """Print the public desktop client ID, never a token or secret."""
    print(PLAYBACK_CLIENT_ID)


def main():
    """Only explicit operations are supported; no ambient auth changes."""
    if len(sys.argv) != 2:
        print(
            "Usage: connect_playback_auth.py authorize|status|client-id|pipe-token|pipe-dual"
        )
        return 2
    commands = {
        "authorize": authorize,
        "status": status,
        "client-id": print_client_id,
        "pipe-token": pipe_token,
        "pipe-dual": dual_tokens,
    }
    operation = commands.get(sys.argv[1])
    if operation is None:
        print("Unknown operation.", file=sys.stderr)
        return 2
    os.umask(0o077)
    try:
        return operation() or 0
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        requests.RequestException,
    ) as error:
        # Never include raw OAuth responses, request payloads, or redirect URLs.
        if isinstance(error, ValueError):
            message = str(error)
            if not message.startswith(
                (
                    "Playback pairing",
                    "Playback OAuth refresh",
                    "No experimental",
                    "Refusing to print",
                    "Personal Web API cache",
                )
            ):
                message = "invalid or unavailable playback grant"
        else:
            message = type(error).__name__
        print("FAIL: " + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
