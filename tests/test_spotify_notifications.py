"""Regression tests for owner notifications when Spotify authorization expires."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import requests
from spotipy.exceptions import SpotifyOauthError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bjornify"))

from spotify_auth import SpotifyAuthorizationRequired
from spotify_notifications import SpotifyAuthNotifier


class SpotifyNotificationTests(unittest.TestCase):
    """Verify one-time Discord alerts without contacting Spotify or Discord."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.marker = Path(self.directory.name) / "alert-sent"
        self.owner = MagicMock()
        self.owner.send = AsyncMock()
        self.channel = MagicMock()
        self.channel.send = AsyncMock()
        self.bot = MagicMock()
        self.bot.application_info = AsyncMock(
            return_value=SimpleNamespace(owner=SimpleNamespace(id=12345))
        )
        self.bot.get_user.return_value = self.owner
        self.bot.fetch_user = AsyncMock(return_value=self.owner)
        self.bot.get_channel.return_value = self.channel
        self.bot.fetch_channel = AsyncMock(return_value=self.channel)
        self.auth = MagicMock()
        self.auth.cache_handler.get_cached_token.return_value = {
            "access_token": "access",
            "refresh_token": "refresh",
        }
        self.auth.is_token_expired.return_value = True
        self.notice = SpotifyAuthNotifier(
            self.bot, self.auth, marker_path=self.marker, channel_id="6789"
        )

    def test_no_refresh_authorization_sends_one_dm(self):
        """No cache triggers an owner DM, but not another on repeated checks."""
        self.auth.cache_handler.get_cached_token.return_value = None

        async def run():
            await self.notice.check_authorization()
            await self.notice.check_authorization()

        asyncio.run(run())
        self.owner.send.assert_awaited_once()
        self.assertIn("/spotify connect", self.owner.send.await_args.args[0])
        self.assertTrue(self.marker.exists())
        self.channel.send.assert_not_awaited()

    def test_restarts_do_not_repeat_disconnected_alert(self):
        """The marker on the persistent secrets volume survives bot restarts."""
        self.marker.touch()
        again = SpotifyAuthNotifier(
            self.bot, self.auth, marker_path=self.marker, channel_id="6789"
        )
        asyncio.run(again.send_alert())
        self.owner.send.assert_not_awaited()

    def test_invalid_refresh_grant_triggers_alert(self):
        """Spotify's expired-grant signal triggers an owner notification."""
        self.auth.refresh_access_token.side_effect = SpotifyAuthorizationRequired(
            "invalid_grant"
        )
        asyncio.run(self.notice.check_authorization())
        self.owner.send.assert_awaited_once()
        self.assertTrue(self.marker.exists())

    def test_temporary_refresh_failure_does_not_notify(self):
        """A temporary Spotify outage must not be described as token expiry."""
        self.auth.refresh_access_token.side_effect = SpotifyOauthError(
            "temporary", error="server_error"
        )
        asyncio.run(self.notice.check_authorization())
        self.owner.send.assert_not_awaited()
        self.assertFalse(self.marker.exists())

    def test_temporary_http_error_does_not_notify(self):
        """A network failure must not stop monitoring or send an expiry alert."""
        self.auth.refresh_access_token.side_effect = requests.ConnectionError(
            "Spotify temporarily unavailable"
        )
        asyncio.run(self.notice.check_authorization())
        self.owner.send.assert_not_awaited()
        self.assertFalse(self.marker.exists())

    def test_owner_lookup_outage_falls_back_to_channel(self):
        """Discord app-info failures can still notify the command channel."""
        response = MagicMock(status=503, reason="Service Unavailable")
        self.bot.application_info.side_effect = discord.HTTPException(
            response, "Discord temporarily unavailable"
        )
        asyncio.run(self.notice.send_alert())
        self.channel.send.assert_awaited_once()
        self.assertIn("/spotify connect", self.channel.send.await_args.args[0])
        self.assertTrue(self.marker.exists())

    def test_healthy_refresh_clears_previous_alert(self):
        """Successful refresh allows a new alert after a future expiration."""
        self.marker.touch()
        asyncio.run(self.notice.check_authorization())
        self.auth.refresh_access_token.assert_called_once_with("refresh")
        self.assertFalse(self.marker.exists())
        self.owner.send.assert_not_awaited()

    def test_valid_access_token_does_not_need_refresh(self):
        """Normal valid access tokens never produce a reconnection warning."""
        self.auth.is_token_expired.return_value = False
        asyncio.run(self.notice.check_authorization())
        self.auth.refresh_access_token.assert_not_called()
        self.owner.send.assert_not_awaited()

    def test_app_owner_override_uses_configured_discord_user(self):
        """Explicit owner override works for applications owned by teams."""
        with patch.dict(os.environ, {"SPOTIFY_NOTIFY_USER_ID": "98765"}):
            self.bot.get_user.return_value = None
            asyncio.run(self.notice.send_alert())
        self.bot.fetch_user.assert_awaited_once_with(98765)
        self.owner.send.assert_awaited_once()
        self.bot.application_info.assert_not_awaited()

    def test_direct_message_denied_falls_back_to_command_channel(self):
        """DM-disabled owners can still receive the reminder in the bot channel."""
        response = MagicMock(status=403, reason="Forbidden")
        self.owner.send.side_effect = discord.Forbidden(response, "DM disabled")
        asyncio.run(self.notice.send_alert())
        self.channel.send.assert_awaited_once()
        message = self.channel.send.await_args.args[0]
        self.assertIn("<@12345>", message)
        self.assertIn("/spotify connect", message)
        self.assertTrue(self.marker.exists())

    def test_delivery_failure_does_not_create_marker(self):
        """An unavailable Discord API is retried rather than marked delivered."""
        response = MagicMock(status=503, reason="Service Unavailable")
        self.owner.send.side_effect = discord.HTTPException(response, "Offline")
        self.channel.send.side_effect = discord.HTTPException(response, "Offline")
        asyncio.run(self.notice.send_alert())
        self.assertFalse(self.marker.exists())

    def test_successful_reconnect_resets_marker(self):
        """A callback-success event permits future expiry notifications."""
        self.marker.touch()
        self.notice.clear_alert()
        asyncio.run(self.notice.send_alert())
        self.owner.send.assert_awaited_once()
        self.assertTrue(self.marker.exists())

    def test_alert_scheduled_safely_from_worker_thread(self):
        """A synchronous playback error can schedule an async Discord notice."""

        async def run():
            notified = asyncio.Event()

            async def owner_send(_text):
                notified.set()

            self.owner.send.side_effect = owner_send
            self.notice._loop = asyncio.get_running_loop()
            await asyncio.to_thread(self.notice.schedule_alert)
            await asyncio.wait_for(notified.wait(), timeout=2)
            await asyncio.sleep(0)

        asyncio.run(run())
        self.owner.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
