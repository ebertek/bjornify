"""Spotify authorization regression tests without live credentials or browser access."""

import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from spotipy.exceptions import SpotifyOauthError
from spotipy.oauth2 import CacheFileHandler, SpotifyOAuth

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bjornify"))
spotify_auth = importlib.import_module("spotify_auth")


class SpotifyAuthTests(unittest.TestCase):
    """Exercise the headless cache and safe browser reauthorization flow."""

    def make_manager(self, cache_file):
        """Create an actual OAuth manager without invoking Spotify."""
        return spotify_auth.HeadlessSpotifyOAuth(
            client_id="test-id",
            client_secret="test-secret",
            redirect_uri="http://127.0.0.1:3000",
            scope=spotify_auth.SCOPE,
            open_browser=False,
            cache_handler=CacheFileHandler(cache_path=str(cache_file)),
        )

    def test_api_client_never_automatically_retries_429_or_writes(self):
        """urllib3 must preserve 429 responses and not replay playback writes."""
        with patch.object(spotify_auth, "make_auth_manager", return_value=MagicMock()):
            client = spotify_auth.make_spotify_client()
        retry = client._session.get_adapter(
            "https://"
        ).max_retries  # pylint: disable=protected-access
        self.assertFalse(retry.respect_retry_after_header)
        self.assertNotIn(429, retry.status_forcelist)
        self.assertEqual(retry.allowed_methods, frozenset({"GET"}))
        client._session.close()  # pylint: disable=protected-access

    def test_compact_diagnostics_show_device_and_playback_without_ids(self):
        """The normal diagnostics output is readable and identifier-free."""
        client = MagicMock()
        client.devices.return_value = {
            "devices": [
                {
                    "id": "synthetic-private-id",
                    "name": "Test Phone",
                    "type": "Smartphone",
                    "is_active": True,
                    "is_restricted": False,
                }
            ]
        }
        client.current_playback.return_value = {
            "device": {"id": "synthetic-private-id", "name": "Test Phone"},
            "is_playing": False,
            "item": {
                "name": "Test Track",
                "artists": [{"name": "Test Artist"}],
                "duration_ms": 200000,
            },
            "progress_ms": 12345,
        }
        with (
            patch.object(spotify_auth, "make_spotify_client", return_value=client),
            io.StringIO() as output,
            redirect_stdout(output),
        ):
            spotify_auth.show_diagnostics()
            text = output.getvalue()
        self.assertNotIn("synthetic-private-id", text)
        report = json.loads(text)
        self.assertFalse(report["current_playback"]["is_playing"])
        self.assertEqual(report["devices"][0]["name"], "Test Phone")
        self.assertEqual(report["current_playback"]["track"], "Test Track")
        client.devices.assert_called_once()
        client.current_playback.assert_called_once()

    def test_full_diagnostics_prints_unmodified_spotify_payloads(self):
        """Full mode is an explicit opt-in for local API inspection."""
        client = MagicMock()
        client.devices.return_value = {"devices": [{"id": "test-id"}]}
        client.current_playback.return_value = None
        with (
            patch.object(spotify_auth, "make_spotify_client", return_value=client),
            io.StringIO() as output,
            redirect_stdout(output),
        ):
            spotify_auth.show_diagnostics(full=True)
            text = output.getvalue()
        self.assertIn("test-id", text)
        self.assertIn("=== CURRENT PLAYBACK ===", text)
        self.assertIn("null", text)

    def test_discord_callback_saves_private_token_cache(self):
        """Newly cached refresh tokens are readable only by their owner."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "spotify-cache"
            handler = spotify_auth.PrivateTokenCache(cache_path=str(path))
            handler.save_token_to_cache(
                {"access_token": "dummy", "refresh_token": "dummy"}
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_default_loopback_redirect(self):
        """Default callback uses Spotify's supported loopback IP literal."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                spotify_auth.redirect_uri_from_environment(),
                "http://127.0.0.1:3000",
            )

    def test_rejects_localhost_redirect(self):
        """Old localhost callback must not silently break new authorization."""
        with (
            patch.dict(os.environ, {"SPOTIPY_REDIRECT_URI": "http://localhost:3000"}),
            self.assertRaisesRegex(ValueError, "no longer supported"),
        ):
            spotify_auth.redirect_uri_from_environment()

    def test_no_cache_never_prompts_in_background(self):
        """The bot must not block on input when authorization is missing."""
        with tempfile.TemporaryDirectory() as temporary:
            manager = self.make_manager(Path(temporary) / "token")
            with self.assertRaises(spotify_auth.SpotifyAuthorizationRequired):
                manager.get_access_token(as_dict=False)

    def test_invalid_grant_discards_unusable_refresh_token(self):
        """Expired six-month refresh tokens must clear only their own cache."""
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "token"
            cache.write_text('{"refresh_token": "expired"}')
            manager = self.make_manager(cache)
            error = SpotifyOauthError("expired", error="invalid_grant")
            with (
                patch.object(SpotifyOAuth, "refresh_access_token", side_effect=error),
                self.assertRaises(spotify_auth.SpotifyAuthorizationRequired),
            ):
                manager.refresh_access_token("expired")
            self.assertFalse(cache.exists())

    def test_transient_oauth_errors_preserve_cache(self):
        """Server errors should not force needless reauthorization."""
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "token"
            cache.write_text('{"refresh_token": "valid"}')
            manager = self.make_manager(cache)
            error = SpotifyOauthError("server error", error="server_error")
            with (
                patch.object(SpotifyOAuth, "refresh_access_token", side_effect=error),
                self.assertRaises(SpotifyOauthError),
            ):
                manager.refresh_access_token("valid")
            self.assertTrue(cache.exists())

    def test_valid_redirect_url_saves_authorization(self):
        """Authorized URL with correct state is exchanged exactly once."""
        state = "test-state"
        url = "http://127.0.0.1:3000/?code=one-time-code&state=test-state"
        manager = MagicMock(redirect_uri="http://127.0.0.1:3000")
        manager.parse_auth_response_url.return_value = (state, "one-time-code")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "token"
            cache.write_text("{}")
            with (
                patch.object(spotify_auth.sys, "stdin", MagicMock(isatty=lambda: True)),
                patch.object(spotify_auth.secrets, "token_urlsafe", return_value=state),
                patch.object(spotify_auth, "make_auth_manager", return_value=manager),
                patch.object(spotify_auth.getpass, "getpass", return_value=url),
                patch.object(spotify_auth, "TOKEN_CACHE_PATH", cache),
            ):
                spotify_auth.authorize_interactively()
            manager.get_access_token.assert_called_once_with(
                code="one-time-code", check_cache=False, as_dict=False
            )
            self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    def test_rejects_mismatched_state_without_exchanging_token(self):
        """A malicious redirected URL cannot exchange an unverified code."""
        manager = MagicMock(redirect_uri="http://127.0.0.1:3000")
        manager.parse_auth_response_url.return_value = ("wrong-state", "code")
        with (
            patch.object(spotify_auth.sys, "stdin", MagicMock(isatty=lambda: True)),
            patch.object(
                spotify_auth.secrets, "token_urlsafe", return_value="expected-state"
            ),
            patch.object(spotify_auth, "make_auth_manager", return_value=manager),
            patch.object(
                spotify_auth.getpass,
                "getpass",
                return_value="http://127.0.0.1:3000/?code=code&state=wrong-state",
            ),
            self.assertRaisesRegex(ValueError, "state mismatch"),
        ):
            spotify_auth.authorize_interactively()
        manager.get_access_token.assert_not_called()

    def test_rejects_foreign_callback_without_exchanging_token(self):
        """A foreign domain or port cannot be used as the callback URL."""
        manager = MagicMock(redirect_uri="http://127.0.0.1:3000")
        with (
            patch.object(spotify_auth.sys, "stdin", MagicMock(isatty=lambda: True)),
            patch.object(spotify_auth, "make_auth_manager", return_value=manager),
            patch.object(
                spotify_auth.getpass,
                "getpass",
                return_value="https://attacker.example/?code=foo",
            ),
            self.assertRaisesRegex(ValueError, "does not match"),
        ):
            spotify_auth.authorize_interactively()
        manager.get_access_token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
