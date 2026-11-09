"""Regression tests for production Spotify Connect restricted-Sonos fallback.

No test contacts Spotify, Sonos, Discord, or a real librespot process.
"""

import json
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bjornify"))

from spotify_connect_queue import (  # noqa: E402  # pylint: disable=wrong-import-position
    ConnectQueueError,
    get_receiver_id,
    personal_access_token,
    private_sonos_ip,
    queue_restricted_sonos,
)

SONOS_ID = "0123456789abcdef0123456789abcdef01234567"
TRACK_URI = "spotify:track:76JhicgPtW4zbN72tpnMyA"


class MockReply:  # pylint: disable=too-few-public-methods
    """Small streaming ZeroConf response supporting context management."""

    def __init__(self, data, status=200):
        self.status_code = status
        self.data = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, _size):
        """Return the mocked ZeroConf response exactly once."""
        yield self.data


class ConnectQueueTests(unittest.TestCase):
    """Only allow a verified active Sonos to receive one queue command."""

    def setUp(self):
        """Prepare a playing, ungrouped receiver and distinct test grants."""
        self.playback = {
            "is_playing": True,
            "device": {
                "name": "Sunroom",
                "type": "Speaker",
                "is_active": True,
                "is_restricted": True,
                "id": None,
            },
        }
        self.speaker = MagicMock()
        self.speaker.player_name = "Sunroom"
        self.speaker.ip_address = "192.168.2.47"
        self.speaker.group.coordinator = self.speaker
        self.speaker.group.members = [self.speaker]
        self.speaker.get_current_transport_info.return_value = {
            "current_transport_state": "PLAYING"
        }
        self.auth = MagicMock()
        self.auth.cache_handler.get_cached_token.return_value = {
            "refresh_token": "personal-refresh",
            "scope": "user-read-playback-state",
        }
        self.auth.get_access_token.return_value = "personal-access"
        self.playback_module = types.ModuleType("connect_playback_auth")
        self.playback_module.PLAYBACK_CLIENT_ID = "desktop-client-id"
        self.playback_module.valid_grant = MagicMock(
            return_value={"access_token": "playback-access"}
        )

    def test_only_private_lan_ipv4_is_accepted(self):
        """Reject DNS hostnames, loopback, public IPv4 and IPv6."""
        self.assertEqual(private_sonos_ip("192.168.2.47"), "192.168.2.47")
        for address in ("localhost", "1.1.1.1", "127.0.0.1", "::1", "169.254.2.3"):
            self.assertIsNone(private_sonos_ip(address))

    def test_receiver_getinfo_drops_other_fields(self):
        """Read a bounded reply without disclosing any Sonos account metadata."""
        session = MagicMock()
        session.get.return_value = MockReply(
            {
                "remoteName": "Sunroom",
                "deviceType": "SPEAKER",
                "deviceID": SONOS_ID,
                "publicKey": "secret",
                "activeUser": "secret",
            }
        )
        with patch("spotify_connect_queue.requests.Session", return_value=session):
            name, kind, identity = get_receiver_id("192.168.2.47")
        self.assertEqual((name, kind, identity), ("Sunroom", "SPEAKER", SONOS_ID))
        self.assertNotIn("secret", repr((name, kind, identity)))
        self.assertEqual(
            session.get.call_args.args[0],
            "http://192.168.2.47:1400/spotifyzc",
        )
        self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
        session.close.assert_called_once()

    def test_receiver_invalid_identity_refuses(self):
        """Never target a receiver with a malformed Connect ID."""
        session = MagicMock()
        session.get.return_value = MockReply(
            {"remoteName": "Sunroom", "deviceID": "bad"}
        )
        with (
            patch("spotify_connect_queue.requests.Session", return_value=session),
            self.assertRaises(ConnectQueueError),
        ):
            get_receiver_id("192.168.2.47")

    def test_personal_web_api_token_is_refreshed_by_its_own_auth_manager(self):
        """Production manager retains authority over personal token refresh."""
        token = personal_access_token(self.auth)
        self.assertEqual(token, "personal-access")
        self.auth.get_access_token.assert_called_once_with(as_dict=False)

    def test_one_connect_queue_write_when_all_checks_match(self):
        """Desktop token goes to Connect and personal token only to Web API."""
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="SENT: one Connect-state queue command to Sunroom.",
            stderr="",
        )
        with (
            patch("spotify_connect_queue.soco.discover", return_value={self.speaker}),
            patch(
                "spotify_connect_queue.get_receiver_id",
                return_value=("Sunroom", "SPEAKER", SONOS_ID),
            ),
            patch.dict(sys.modules, {"connect_playback_auth": self.playback_module}),
            patch(
                "spotify_connect_queue.subprocess.run", return_value=result
            ) as command,
        ):
            self.assertTrue(queue_restricted_sonos(TRACK_URI, self.playback, self.auth))
        command.assert_called_once()
        args, kwargs = command.call_args
        self.assertEqual(
            args[0][2:7], ["add-sonos", "Sunroom", "192.168.2.47", SONOS_ID, TRACK_URI]
        )
        self.assertEqual(args[0][-1], "--confirm-write")
        self.assertNotIn("personal-access", repr(args))
        self.assertNotIn("playback-access", repr(args))
        self.assertEqual(
            json.loads(kwargs["input"]),
            {"playback_token": "playback-access", "web_api_token": "personal-access"},
        )
        self.assertFalse(kwargs["check"])

    def test_refuse_inactive_or_unrestricted_session_before_discovery(self):
        """Missing device assertions must never reach Sonos or the subprocess."""
        for device_changes in (
            {"is_active": False},
            {"is_restricted": False},
            {"type": "Computer"},
        ):
            playback = {
                **self.playback,
                "device": {**self.playback["device"], **device_changes},
            }
            with patch("spotify_connect_queue.soco.discover") as discovery:
                with self.assertRaises(ConnectQueueError):
                    queue_restricted_sonos(TRACK_URI, playback, self.auth)
                discovery.assert_not_called()

    def test_grouped_receiver_refuses_queue(self):
        """Queue routing is not yet proven safe for grouped Sonos rooms."""
        self.speaker.group.members = [self.speaker, MagicMock()]
        with (
            patch("spotify_connect_queue.soco.discover", return_value={self.speaker}),
            patch("spotify_connect_queue.subprocess.run") as command,
            self.assertRaises(ConnectQueueError),
        ):
            queue_restricted_sonos(TRACK_URI, self.playback, self.auth)
        command.assert_not_called()

    def test_uncertain_connect_write_is_not_retried(self):
        """A failed private API response must not cause duplicate queue writes."""
        with (
            patch("spotify_connect_queue.soco.discover", return_value={self.speaker}),
            patch(
                "spotify_connect_queue.get_receiver_id",
                return_value=("Sunroom", "SPEAKER", SONOS_ID),
            ),
            patch.dict(sys.modules, {"connect_playback_auth": self.playback_module}),
            patch(
                "spotify_connect_queue.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "", "secret"),
            ) as command,
            self.assertRaises(ConnectQueueError),
        ):
            queue_restricted_sonos(TRACK_URI, self.playback, self.auth)
        command.assert_called_once()

    def test_missing_playback_grant_never_starts_process(self):
        """Explicit device pairing is required before any Connect write."""
        self.playback_module.valid_grant.side_effect = ValueError(
            "Missing playback cache"
        )
        with (
            patch("spotify_connect_queue.soco.discover", return_value={self.speaker}),
            patch(
                "spotify_connect_queue.get_receiver_id",
                return_value=("Sunroom", "SPEAKER", SONOS_ID),
            ),
            patch.dict(sys.modules, {"connect_playback_auth": self.playback_module}),
            patch("spotify_connect_queue.subprocess.run") as command,
            self.assertRaises(ConnectQueueError),
        ):
            queue_restricted_sonos(TRACK_URI, self.playback, self.auth)
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
