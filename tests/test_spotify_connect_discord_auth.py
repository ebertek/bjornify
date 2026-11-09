"""Tests for private Discord Spotify Connect device pairing."""

import asyncio
import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bjornify"))
pairing = importlib.import_module("spotify_connect_discord_auth")


class DiscordPlaybackPairingTests(unittest.IsolatedAsyncioTestCase):
    """Verify async pairing completion, owner session exclusivity and token safety."""

    def setUp(self):
        """Supply a fake Spotify API; tests never make network calls."""
        self.oauth = types.ModuleType("connect_playback_auth")
        self.oauth.begin_pairing = MagicMock(
            return_value={
                "device_code": "confidential-device-code",
                "user_code": "ABC123",
                "expires_in": 600,
                "interval": 5,
            }
        )
        self.oauth.poll_pairing = MagicMock(
            return_value={
                "access_token": "confidential-access-token",
                "refresh_token": "confidential-refresh-token",
                "expires_in": 3600,
                "scope": "streaming",
            }
        )
        self.oauth.save_grant = MagicMock()
        self.oauth.read_grant = MagicMock(
            return_value={"refresh_token": "secret", "scope": "streaming"}
        )
        self.patch_modules = patch.dict(
            sys.modules, {"connect_playback_auth": self.oauth}
        )
        self.patch_modules.start()
        self.addCleanup(self.patch_modules.stop)
        self.service = pairing.DiscordPlaybackPairings()

    async def test_private_pairing_saves_grant_and_reports_only_success(self):
        """Only temporary user code is returned to Discord, not device secret."""
        notify = AsyncMock()
        with patch.object(pairing.asyncio, "sleep", new_callable=AsyncMock):
            visible = await self.service.begin(notify)
            await self.service._task
        self.assertEqual(visible, {"user_code": "ABC123", "expires_in": 600})
        self.assertNotIn("confidential-device-code", repr(visible))
        self.oauth.poll_pairing.assert_called_once_with("confidential-device-code")
        self.oauth.save_grant.assert_called_once()
        notify.assert_awaited_once()
        self.assertTrue(notify.await_args.args[0])
        message = notify.await_args.args[1]
        self.assertNotIn("confidential", message)
        self.assertFalse(self.service.pending)

    async def test_pending_rejects_second_pairing(self):
        """Concurrent Discord commands cannot create competing grants."""
        notify = AsyncMock()
        with patch.object(pairing.asyncio, "sleep", new_callable=AsyncMock):
            await self.service.begin(notify)
            with self.assertRaises(pairing.PairingInProgress):
                await self.service.begin(notify)
            await self.service._task
        self.oauth.begin_pairing.assert_called_once()

    async def test_denied_pairing_never_writes_credentials(self):
        """User denial produces a private error without storing any grant."""
        self.oauth.poll_pairing.return_value = {
            "error": "access_denied",
            "error_description": "sensitive-account-details",
        }
        notify = AsyncMock()
        with patch.object(pairing.asyncio, "sleep", new_callable=AsyncMock):
            await self.service.begin(notify)
            await self.service._task
        self.oauth.save_grant.assert_not_called()
        self.assertFalse(notify.await_args.args[0])
        self.assertNotIn("sensitive-account-details", notify.await_args.args[1])

    async def test_slow_down_increases_interval_without_extra_writes(self):
        """Spotify rate limiting extends the poll interval rather than retrying fast."""
        self.oauth.poll_pairing.side_effect = [
            {"error": "slow_down"},
            {"error": "authorization_pending"},
            {
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
                "scope": "streaming",
            },
        ]
        notify = AsyncMock()
        with patch.object(pairing.asyncio, "sleep", new_callable=AsyncMock) as sleep:
            await self.service.begin(notify)
            await self.service._task
        self.assertEqual(sleep.await_args_list[0].args[0], 5)
        self.assertEqual(sleep.await_args_list[1].args[0], 10)
        self.oauth.save_grant.assert_called_once()
        self.assertTrue(notify.await_args.args[0])

    async def test_network_failure_is_sanitized(self):
        """Exceptions containing raw service details never appear in status."""
        self.oauth.poll_pairing.side_effect = ValueError("secret-device-code")
        notify = AsyncMock()
        with patch.object(pairing.asyncio, "sleep", new_callable=AsyncMock):
            await self.service.begin(notify)
            await self.service._task
        self.assertFalse(notify.await_args.args[0])
        self.assertNotIn("secret-device-code", notify.await_args.args[1])

    def test_status_never_refreshes_or_discloses_grants(self):
        """Status reads cache metadata only."""
        self.assertIn("Connected", pairing.playback_grant_status())
        self.oauth.read_grant.assert_called_once()
        self.oauth.read_grant.side_effect = ValueError("secret-error")
        self.assertIn("Not connected", pairing.playback_grant_status())


if __name__ == "__main__":
    unittest.main()
