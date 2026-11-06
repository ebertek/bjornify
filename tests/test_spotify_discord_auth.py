"""Regression tests for owner-only Spotify login and HTTPS callback handling."""

# pylint: disable=protected-access

import asyncio
import http.client
import importlib
import os
import sys
import unittest
from http.server import ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bjornify"))
login = importlib.import_module("spotify_discord_auth")


class DiscordSpotifyLoginTests(unittest.TestCase):
    """Test authorization ownership, expiration, and single-use callbacks."""

    def setUp(self):
        self.sessions = login.SpotifyAuthorizations()
        self.manager = MagicMock()
        self.manager.get_authorize_url.return_value = "https://spotify.test/authorize"

    def begin(self, owner_id=123, on_complete=None):
        """Start a mocked Spotify authorization session for an owner."""
        with (
            patch.object(login, "make_auth_manager", return_value=self.manager),
            patch.object(login.secrets, "token_urlsafe", return_value="secure-state"),
        ):
            return self.sessions.begin(owner_id, on_complete=on_complete)

    def callback_response(self, path):
        """Make a real local HTTP request and capture sanitized OAuth logs."""
        with (
            patch.dict(
                os.environ,
                {
                    "SPOTIPY_REDIRECT_URI": "https://spotify.example.com/spotify/callback"
                },
            ),
            patch.object(
                login,
                "ThreadingHTTPServer",
                side_effect=lambda address, handler: ThreadingHTTPServer(
                    ("127.0.0.1", 0), handler
                ),
            ),
        ):
            server = login.start_spotify_callback(self.sessions)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                with self.assertLogs(login._LOGGER, level="INFO") as events:
                    conn.request("GET", path)
                    response = conn.getresponse()
                    status = response.status
                    body = response.read().decode("utf-8")
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
        return status, body, events.output

    def test_success_logs_info_without_oauth_secrets(self):
        """Successful Spotify auth is visible at INFO without leaking tokens."""
        self.begin()
        status, _, events = self.callback_response(
            "/spotify/callback?state=secure-state&code=one-time-code"
        )
        self.assertEqual(status, 200)
        self.assertTrue(
            any("INFO" in line and "completed successfully" in line for line in events)
        )
        self.assertNotIn("one-time-code", " ".join(events))
        self.assertNotIn("secure-state", " ".join(events))

    def test_spotify_denial_logs_warning_without_state(self):
        """An owner refusing Spotify authorization is visible as a warning."""
        self.begin()
        status, _, events = self.callback_response(
            "/spotify/callback?state=secure-state&error=access_denied"
        )
        self.assertEqual(status, 400)
        self.assertTrue(
            any("WARNING" in line and "rejected" in line for line in events)
        )
        self.assertNotIn("secure-state", " ".join(events))
        self.manager.get_access_token.assert_not_called()

    def test_token_exchange_failure_logs_safely(self):
        """OAuth exchange errors produce ERROR without printing exception secrets."""
        self.begin()
        self.manager.get_access_token.side_effect = RuntimeError(
            "Sensitive Spotify callback code"
        )
        status, _, events = self.callback_response(
            "/spotify/callback?state=secure-state&code=one-time-code"
        )
        self.assertEqual(status, 502)
        self.assertTrue(
            any("ERROR" in line and "RuntimeError" in line for line in events)
        )
        self.assertNotIn("Sensitive Spotify callback code", " ".join(events))
        self.assertNotIn("one-time-code", " ".join(events))

    def test_missing_callback_state_logs_warning(self):
        """Malformed callback requests are visible at WARNING level."""
        status, _, events = self.callback_response("/spotify/callback?code=invalid")
        self.assertEqual(status, 400)
        self.assertTrue(any("WARNING" in line for line in events))
        self.assertNotIn("invalid", " ".join(events))

    def test_authorization_link_is_created_for_the_owner(self):
        """A fresh login link is generated with the session owner's state."""
        self.assertEqual(self.begin(), "https://spotify.test/authorize")
        self.manager.get_authorize_url.assert_called_once()
        self.assertEqual(self.sessions._pending["secure-state"].owner_id, 123)

    def test_owner_can_complete_a_login_once(self):
        """An accepted callback exchanges a code once; replay is rejected."""
        self.begin()
        self.sessions.finish("secure-state", "one-time-code", owner_id=123)
        self.manager.get_access_token.assert_called_once_with(
            code="one-time-code", check_cache=False, as_dict=False
        )
        with self.assertRaises(ValueError):
            self.sessions.finish("secure-state", "one-time-code", owner_id=123)

    def test_success_notifies_owner_exactly_once(self):
        """Successful authorization sends only a status boolean to Discord."""
        notify = MagicMock()
        self.begin(on_complete=notify)
        self.sessions.finish("secure-state", "one-time-code", owner_id=123)
        notify.assert_called_once_with(True)
        with self.assertRaises(ValueError):
            self.sessions.finish("secure-state", "one-time-code", owner_id=123)
        notify.assert_called_once_with(True)

    def test_refused_authorization_notifies_owner(self):
        """Spotify refusal updates Discord even though the browser receives 400."""
        notify = MagicMock()
        self.begin(on_complete=notify)
        with self.assertRaises(ValueError):
            self.sessions.finish("secure-state", error="access_denied")
        notify.assert_called_once_with(False)
        self.manager.get_access_token.assert_not_called()

    def test_token_exchange_failure_notifies_owner(self):
        """Token exchange errors remove the stale button and report failure."""
        notify = MagicMock()
        self.begin(on_complete=notify)
        self.manager.get_access_token.side_effect = RuntimeError("exchange failure")
        with self.assertRaises(RuntimeError):
            self.sessions.finish("secure-state", "one-time-code")
        notify.assert_called_once_with(False)

    def test_discord_notification_failure_does_not_undo_login(self):
        """Discord outages cannot turn a completed Spotify login into HTTP 502."""
        notify = MagicMock(side_effect=RuntimeError("Discord unavailable"))
        self.begin(on_complete=notify)
        self.sessions.finish("secure-state", "one-time-code")
        self.manager.get_access_token.assert_called_once()
        notify.assert_called_once_with(True)

    def test_other_discord_users_cannot_finish_login(self):
        """The session is tied to the initiating Discord application owner."""
        self.begin()
        with self.assertRaises(PermissionError):
            self.sessions.finish("secure-state", "one-time-code", owner_id=456)
        self.manager.get_access_token.assert_not_called()

    def test_expired_login_is_rejected(self):
        """A pending authorization cannot be exchanged after ten minutes."""
        self.begin()
        self.sessions._pending["secure-state"].expires_at = 0
        with self.assertRaises(ValueError):
            self.sessions.finish("secure-state", "one-time-code")
        self.manager.get_access_token.assert_not_called()

    def test_superseded_login_is_rejected(self):
        """Starting a second login invalidates the first owner's session."""
        self.begin()
        with (
            patch.object(login, "make_auth_manager", return_value=self.manager),
            patch.object(login.secrets, "token_urlsafe", return_value="new-state"),
        ):
            self.sessions.begin(123)
        with self.assertRaises(ValueError):
            self.sessions.finish("secure-state", "old-code")
        self.assertIn("new-state", self.sessions._pending)

    def test_denied_login_does_not_exchange_code(self):
        """A Spotify refusal ends the session without an access-token call."""
        self.begin()
        with self.assertRaises(ValueError):
            self.sessions.finish("secure-state", error="access_denied")
        self.manager.get_access_token.assert_not_called()

    def test_https_callback_completes_and_never_exposes_code(self):
        """An actual local HTTP callback returns only a generic success page."""
        self.begin()
        with (
            patch.dict(
                os.environ,
                {
                    "SPOTIPY_REDIRECT_URI": "https://spotify.example.com/spotify/callback",
                    "SPOTIFY_AUTH_CALLBACK_PORT": "8765",
                },
            ),
            patch.object(
                login,
                "ThreadingHTTPServer",
                side_effect=lambda address, handler: ThreadingHTTPServer(
                    ("127.0.0.1", 0), handler
                ),
            ),
        ):
            server = login.start_spotify_callback(self.sessions)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request(
                    "GET",
                    "/spotify/callback?state=secure-state&code=one-time-code",
                )
                response = conn.getresponse()
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("Spotify connected", body)
                self.assertNotIn("one-time-code", body)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                conn.close()

                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request(
                    "GET",
                    "/spotify/callback?state=secure-state&code=one-time-code",
                )
                retry = conn.getresponse()
                self.assertEqual(retry.status, 400)
                retry.read()
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
        self.manager.get_access_token.assert_called_once()

    def test_wrong_callback_path_never_exchanges_code(self):
        """Only the configured callback path can finish Spotify authorization."""
        self.begin()
        with (
            patch.dict(
                os.environ,
                {"SPOTIPY_REDIRECT_URI": "https://spotify.example.com/callback"},
            ),
            patch.object(
                login,
                "ThreadingHTTPServer",
                side_effect=lambda address, handler: ThreadingHTTPServer(
                    ("127.0.0.1", 0), handler
                ),
            ),
        ):
            server = login.start_spotify_callback(self.sessions)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
                conn.request("GET", "/wrong?state=secure-state&code=one-time-code")
                result = conn.getresponse()
                self.assertEqual(result.status, 404)
                result.read()
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
        self.manager.get_access_token.assert_not_called()


class DiscordSpotifyCommandTests(unittest.TestCase):
    """Exercise application-owner restrictions without contacting Discord."""

    @classmethod
    def setUpClass(cls):
        """Import Björnify with fake credentials and no network calls."""
        with (
            patch.dict(
                os.environ,
                {
                    "SPOTIPY_CLIENT_ID": "test",
                    "SPOTIPY_CLIENT_SECRET": "test",
                    "DISCORD_BOT_TOKEN": "test",
                    "CHANNEL_ID": "123",
                },
            ),
            patch("spotify_auth.make_auth_manager"),
            patch("spotipy.Spotify"),
        ):
            spec = spec_from_file_location(
                "bjornify_discord_auth_tests", ROOT / "bjornify" / "bjornify.py"
            )
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.bot_module = module

    def test_nonowner_cannot_start_authorization(self):
        """A normal Discord member cannot obtain or start an OAuth link."""
        interaction = MagicMock()
        interaction.user.id = 456
        interaction.response.send_message = AsyncMock()
        with (
            patch.object(
                self.bot_module.bot, "is_owner", new=AsyncMock(return_value=False)
            ),
            patch.object(self.bot_module.spotify_authorizations, "begin") as begin,
        ):
            asyncio.run(self.bot_module.spotify_connect.callback(interaction))
        begin.assert_not_called()
        self.assertTrue(
            interaction.response.send_message.await_args.kwargs["ephemeral"]
        )

    def test_success_edits_private_message_and_removes_connect_button(self):
        """The HTTP callback thread updates the existing ephemeral interaction."""
        self._assert_completion_updates_message(
            success=True, expected="✅ Spotify connected!"
        )

    def test_failure_edits_private_message_and_removes_connect_button(self):
        """A failed OAuth exchange also replaces the stale authorization link."""
        self._assert_completion_updates_message(
            success=False, expected="❌ Spotify authorization failed."
        )

    def _assert_completion_updates_message(self, *, success, expected):
        """Exercise thread-safe Discord editing while its event loop is alive."""
        interaction = MagicMock()
        interaction.user.id = 123
        interaction.response.send_message = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        event = asyncio.Event()

        async def perform_edit(**_kwargs):
            event.set()

        interaction.edit_original_response.side_effect = perform_edit
        with (
            patch.object(
                self.bot_module.bot, "is_owner", new=AsyncMock(return_value=True)
            ),
            patch.object(
                self.bot_module.auth_manager,
                "redirect_uri",
                "https://spotify.example.com/spotify/callback",
            ),
            patch.object(
                self.bot_module.spotify_authorizations,
                "begin",
                return_value="https://accounts.spotify.com/authorize?state=test",
            ) as begin,
        ):

            async def connect_then_complete():
                await self.bot_module.spotify_connect.callback(interaction)
                notifier = begin.call_args.kwargs["on_complete"]
                await asyncio.to_thread(notifier, success)
                await asyncio.wait_for(event.wait(), timeout=2)

            asyncio.run(connect_then_complete())

        interaction.response.send_message.assert_awaited_once()
        interaction.edit_original_response.assert_awaited_once_with(
            content=expected
            + (
                " You can now use Björnify."
                if success
                else " Use /spotify connect to retry."
            ),
            view=None,
        )

    def test_owner_gets_private_login_button(self):
        """The authorized owner receives a private link and view."""
        interaction = MagicMock()
        interaction.user.id = 123
        interaction.response.send_message = AsyncMock()
        with (
            patch.object(
                self.bot_module.bot, "is_owner", new=AsyncMock(return_value=True)
            ),
            patch.object(
                self.bot_module.auth_manager,
                "redirect_uri",
                "https://spotify.example.com/spotify/callback",
            ),
            patch.object(
                self.bot_module.spotify_authorizations,
                "begin",
                return_value="https://accounts.spotify.com/authorize?state=test",
            ),
        ):
            asyncio.run(self.bot_module.spotify_connect.callback(interaction))
        values = interaction.response.send_message.await_args
        self.assertTrue(values.kwargs["ephemeral"])
        self.assertEqual(
            values.kwargs["view"].children[0].url,
            "https://accounts.spotify.com/authorize?state=test",
        )


if __name__ == "__main__":
    unittest.main()
