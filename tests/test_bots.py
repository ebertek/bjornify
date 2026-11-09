"""Isolated regression tests for Björnify and the Home Assistant companion bot.

Third-party clients are installed as in production, but all external I/O is mocked.
No Discord, Spotify, Sonos, or Home Assistant credentials are required.
"""

import asyncio
import importlib.util
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import requests
from spotipy.exceptions import SpotifyException

ROOT = Path(__file__).resolve().parents[1]
# Docker runs the scripts from /app; make that import layout available to tests.
sys.path.insert(0, str(ROOT / "bjornify"))
API_ERROR = "Error communicating with Home Assistant."


def load_script(name, path):
    """Load a script from the source tree without running its __main__ block."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BotBehaviorTests(unittest.TestCase):  # pylint: disable=too-many-public-methods
    """Test core bot behavior with fully mocked external services."""

    @classmethod
    def setUpClass(cls):
        """Import both scripts with fake credentials and OAuth clients."""
        cls.original_handlers = logging.getLogger().handlers[:]
        cls.env = patch.dict(
            os.environ,
            {
                "SPOTIPY_CLIENT_ID": "test-client",
                "SPOTIPY_CLIENT_SECRET": "test-secret",
                "DISCORD_BOT_TOKEN": "test-bot",
                "CHANNEL_ID": "12345",
                "HASS_DISCORD_BOT_TOKEN": "test-ha-bot",
                "HASS_CHANNEL_ID": "67890",
                "HA_URL": "https://homeassistant.invalid/api/conversation/process",
                "HA_ACCESS_TOKEN": "test-ha-token",
                "DEFAULT_DEVICE": "Everywhere",
                "LOG_OUTPUT": "console",
            },
        )
        cls.env.start()
        try:
            with (
                patch("spotify_auth.make_auth_manager"),
                patch("spotipy.Spotify"),
            ):
                cls.bjornify = load_script(
                    "bjornify_under_test", ROOT / "bjornify" / "bjornify.py"
                )
                cls.hass = load_script("hass_under_test", ROOT / "bjornify" / "hass.py")
        except Exception:
            cls.env.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        """Restore environment and logging handlers changed by module imports."""
        cls.env.stop()
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            if handler not in cls.original_handlers:
                root_logger.removeHandler(handler)
                handler.close()

    def setUp(self):
        """Patch the global Spotify instance for each test independently."""
        self.spotify = MagicMock()
        self.spotify_patch = patch.object(self.bjornify, "spotify", self.spotify)
        self.spotify_patch.start()
        self.addCleanup(self.spotify_patch.stop)

    def test_queue_track_when_playback_is_active(self):
        """An active player uses Spotify add_to_queue."""
        self.spotify.current_playback.return_value = {"is_playing": True}

        result = self.bjornify.player_add_track("spotify:track:123", "Artist", "Song")

        self.assertEqual(result, "Queued: Artist - Song")
        self.spotify.add_to_queue.assert_called_once_with("spotify:track:123")
        self.spotify.start_playback.assert_not_called()

    def test_restricted_sonos_queue_failure_is_explicit(self):
        """Sonos Spotify Connect 403 must not be reported as an OAuth failure."""
        self.spotify.current_playback.return_value = {
            "is_playing": True,
            "device": {
                "name": "Family room",
                "id": None,
                "is_restricted": True,
            },
        }
        self.spotify.add_to_queue.side_effect = SpotifyException(
            403, -1, "Restricted device"
        )

        with patch.object(self.bjornify, "find_playing_speaker") as find:
            result = self.bjornify.player_add_track(
                "spotify:track:requested", "Slowdive", "Souvlaki Space Station"
            )

        self.assertIn("restricted device", result)
        self.assertIn("NOT queued", result)
        self.assertIn("Spotify app", result)
        self.assertNotIn("Premium", result)
        self.spotify.start_playback.assert_not_called()
        self.spotify.devices.assert_not_called()
        find.assert_not_called()

    def test_restricted_sonos_uses_connect_queue_only_on_specific_403(self):
        """An active restricted Sonos session can queue via Connect."""
        playback = {
            "is_playing": True,
            "device": {
                "name": "Sunroom",
                "id": None,
                "type": "Speaker",
                "is_active": True,
                "is_restricted": True,
            },
        }
        self.spotify.current_playback.return_value = playback
        self.spotify.add_to_queue.side_effect = SpotifyException(
            403, -1, "Restricted device"
        )
        with patch.object(
            self.bjornify, "queue_restricted_sonos", return_value=True
        ) as connect:
            result = self.bjornify.player_add_track(
                "spotify:track:requested", "Slowdive", "Souvlaki Space Station"
            )
        self.assertEqual(
            result,
            "Queued: Slowdive - Souvlaki Space Station (Spotify Connect accepted request)",
        )
        connect.assert_called_once_with(
            "spotify:track:requested", playback, self.bjornify.auth_manager
        )
        self.spotify.add_to_queue.assert_called_once_with("spotify:track:requested")

    def test_connect_queue_failure_does_not_claim_success(self):
        """If Connect refuses the queue, report failure and do not retry."""
        self.spotify.current_playback.return_value = {"is_playing": True}
        self.spotify.add_to_queue.side_effect = SpotifyException(
            403, -1, "Restricted device"
        )
        with patch.object(
            self.bjornify,
            "queue_restricted_sonos",
            side_effect=self.bjornify.ConnectQueueError(
                "Connect authorization missing."
            ),
        ) as connect:
            result = self.bjornify.player_add_track("spotify:track:requested")
        self.assertIn("Connect authorization missing", result)
        self.assertNotIn("Queued:", result)
        connect.assert_called_once()
        self.spotify.add_to_queue.assert_called_once()

    def test_unrelated_queue_403_still_reports_permission_errors(self):
        """A scope or allowlist error should not be called a device restriction."""
        self.spotify.current_playback.return_value = {"is_playing": True}
        self.spotify.add_to_queue.side_effect = SpotifyException(
            403, -1, "Insufficient client scope"
        )

        result = self.bjornify.player_add_track("spotify:track:requested")

        self.assertIn("Premium", result)
        self.assertNotIn("NOT queued", result)

    def test_paused_playback_resumes_original_song_then_queues_next(self):
        """Keep the paused track and target device; never play the new URI now."""
        self.spotify.current_playback.return_value = {
            "is_playing": False,
            "item": {"uri": "spotify:track:original"},
            "device": {"id": "paused-sonos", "name": "Living room"},
        }

        result = self.bjornify.player_add_track(
            "spotify:track:requested", "Artist", "Requested song"
        )

        self.assertEqual(
            result,
            "Queued: Artist - Requested song (resumed paused playback)",
        )
        self.assertEqual(
            self.spotify.mock_calls,
            [
                call.current_playback(),
                call.start_playback(device_id="paused-sonos"),
                call.add_to_queue("spotify:track:requested"),
            ],
        )
        self.spotify.devices.assert_not_called()

    def test_paused_playback_with_null_device_id_uses_active_session(self):
        """Spotify permits nullable device IDs; use its current active session."""
        self.spotify.current_playback.return_value = {
            "is_playing": False,
            "item": {"uri": "spotify:track:original"},
            "device": {"id": None, "name": "CarPlay"},
        }

        result = self.bjornify.player_add_track("spotify:track:requested")

        self.assertEqual(
            result,
            "Queued: spotify:track:requested (resumed paused playback)",
        )
        self.assertEqual(
            self.spotify.mock_calls,
            [
                call.current_playback(),
                call.start_playback(),
                call.add_to_queue("spotify:track:requested"),
            ],
        )
        self.spotify.devices.assert_not_called()

    def test_paused_playback_resume_failure_does_not_queue_or_switch(self):
        """Restricted devices fail explicitly without queueing or switching."""
        self.spotify.current_playback.return_value = {
            "is_playing": False,
            "device": {"id": "restricted"},
        }
        self.spotify.start_playback.side_effect = SpotifyException(
            403, -1, "Restricted device"
        )

        result = self.bjornify.player_add_track("spotify:track:requested")

        self.assertIn("Paused restricted Sonos", result)
        self.spotify.add_to_queue.assert_not_called()
        self.spotify.devices.assert_not_called()
        self.spotify.start_playback.assert_called_once_with(device_id="restricted")

    def test_paused_playback_queue_failure_is_not_claimed_as_success(self):
        """A successful resume must not hide a subsequent queue failure."""
        self.spotify.current_playback.return_value = {
            "is_playing": False,
            "device": {"id": "speaker"},
        }
        self.spotify.add_to_queue.side_effect = SpotifyException(
            429, -1, "Rate limited", headers={"Retry-After": "5"}
        )

        result = self.bjornify.player_add_track("spotify:track:requested")

        self.assertIn("rate-limited", result)
        self.assertNotIn("Queued:", result)
        self.spotify.start_playback.assert_called_once_with(device_id="speaker")

    def test_start_playback_on_preferred_device(self):
        """The configured device wins when there is no current playback."""
        self.spotify.current_playback.return_value = None
        self.spotify.devices.return_value = {
            "devices": [
                {"name": "Kitchen", "id": "first"},
                {"name": "Everywhere", "id": "preferred"},
            ]
        }

        result = self.bjornify.player_add_track("spotify:track:123")

        self.assertEqual(result, "Started playback: spotify:track:123")
        self.spotify.start_playback.assert_called_once_with(
            device_id="preferred", uris=["spotify:track:123"]
        )

    def test_start_playback_on_first_device_if_preferred_missing(self):
        """An unavailable preferred device falls back to the first device."""
        self.spotify.current_playback.return_value = None
        self.spotify.devices.return_value = {
            "devices": [{"name": "Living room", "id": "first"}]
        }

        result = self.bjornify.player_add_track("spotify:track:123")

        self.assertEqual(result, "Started playback: spotify:track:123")
        self.spotify.start_playback.assert_called_once_with(
            device_id="first", uris=["spotify:track:123"]
        )

    def test_skip_restricted_preferred_then_use_eligible_fallback(self):
        """A restricted DEFAULT_DEVICE must not block another usable device."""
        self.spotify.current_playback.return_value = None
        self.spotify.devices.return_value = {
            "devices": [
                {
                    "name": "Everywhere",
                    "id": "blocked",
                    "is_restricted": True,
                },
                {"name": "Living Room", "id": "sonos", "is_restricted": False},
            ]
        }

        result = self.bjornify.player_add_track("spotify:track:123")

        self.assertEqual(result, "Started playback: spotify:track:123")
        self.spotify.start_playback.assert_called_once_with(
            device_id="sonos", uris=["spotify:track:123"]
        )

    def test_skip_unidentified_first_fallback_device(self):
        """An absent device ID is never a valid playback target."""
        self.spotify.current_playback.return_value = None
        self.spotify.devices.return_value = {
            "devices": [
                {"name": "Everywhere", "id": None},
                {"name": "Echo", "id": "echo"},
            ]
        }

        self.bjornify.player_add_track("spotify:track:123")

        self.spotify.start_playback.assert_called_once_with(
            device_id="echo", uris=["spotify:track:123"]
        )

    def test_all_fallback_devices_restricted_or_unidentified(self):
        """Never claim success when Spotify lists only unusable devices."""
        self.spotify.current_playback.return_value = None
        self.spotify.devices.return_value = {
            "devices": [
                {"name": "Everywhere", "id": "blocked", "is_restricted": True},
                {"name": "CarPlay", "id": None},
            ]
        }

        self.assertEqual(
            self.bjornify.player_add_track("spotify:track:123"),
            "No available devices to start playback.",
        )
        self.spotify.start_playback.assert_not_called()

    def test_no_playback_device_returns_a_helpful_response(self):
        """An empty Spotify device list must not attempt playback."""
        self.spotify.current_playback.return_value = None
        self.spotify.devices.return_value = {"devices": []}

        self.assertEqual(
            self.bjornify.player_add_track("spotify:track:123"),
            "No available devices to start playback.",
        )
        self.spotify.start_playback.assert_not_called()

    def test_track_search_queues_the_top_match(self):
        """Text search selects the first result and passes its metadata."""
        self.spotify.search.return_value = {
            "tracks": {
                "items": [
                    {
                        "name": "Song",
                        "uri": "spotify:track:123",
                        "artists": [{"name": "Artist"}],
                    }
                ]
            }
        }
        with patch.object(
            self.bjornify, "player_add_track", return_value="Queued: Artist - Song"
        ) as add:
            response = self.bjornify.player_add_item_to_playback_queue("Song")

        self.assertEqual(response, "Queued: Artist - Song")
        self.spotify.search.assert_called_once_with(q="Song", limit=1, type="track")
        add.assert_called_once_with("spotify:track:123", "Artist", "Song")

    def test_track_search_without_results(self):
        """Empty search results are reported without queueing."""
        self.spotify.search.return_value = {"tracks": {"items": []}}

        self.assertEqual(
            self.bjornify.player_add_item_to_playback_queue("no such song"),
            "No search results.",
        )
        self.spotify.add_to_queue.assert_not_called()

    def test_spotify_api_error_has_user_facing_response(self):
        """Spotify API failures return a message rather than escaping."""
        self.spotify.search.side_effect = SpotifyException(503, -1, "Unavailable")

        self.assertEqual(
            self.bjornify.player_add_item_to_playback_queue("Song"),
            "Spotify is unavailable. Try again later.",
        )

    def test_token_expiration_triggers_refresh_and_retry(self):
        """A 401 response refreshes the token and retries the action once."""
        self.spotify.current_playback.return_value = {"is_playing": True}
        action = MagicMock(side_effect=[SpotifyException(401, -1, "Expired"), None])

        with patch.object(self.bjornify, "refresh_spotify_token") as refresh:
            result = self.bjornify.spotify_action_with_soco_fallback(
                spotify_action=action, soco_action=MagicMock(), action_name="skip"
            )

        self.assertEqual(result, "✅")
        self.assertEqual(action.call_count, 2)
        refresh.assert_called_once_with(force=True)

    def test_restricted_spotify_device_uses_sonos(self):
        """A 403 response can recover through a currently playing Sonos device."""
        self.spotify.current_playback.return_value = {"is_playing": True}
        action = MagicMock(side_effect=SpotifyException(403, -1, "Restricted device"))
        speaker = MagicMock()
        sonos_action = MagicMock()

        with patch.object(self.bjornify, "find_playing_speaker", return_value=speaker):
            result = self.bjornify.spotify_action_with_soco_fallback(
                spotify_action=action,
                soco_action=sonos_action,
                action_name="pause",
            )

        self.assertNotEqual(result, "✅")
        sonos_action.assert_not_called()

    def test_restricted_device_without_sonos_returns_failure(self):
        """A 403 with no Sonos player returns an error reaction."""
        self.spotify.current_playback.return_value = {"is_playing": True}
        action = MagicMock(side_effect=SpotifyException(403, -1, "Restricted device"))
        sonos_action = MagicMock()

        with patch.object(self.bjornify, "find_playing_speaker", return_value=None):
            result = self.bjornify.spotify_action_with_soco_fallback(
                spotify_action=action,
                soco_action=sonos_action,
                action_name="pause",
            )

        self.assertIn("no active Sonos player", result)
        sonos_action.assert_not_called()

    def test_sonos_discovery_ignores_stopped_speakers(self):
        """Sonos discovery skips inactive players and finds a Spotify URI."""
        stopped = MagicMock()
        stopped.get_current_transport_info.return_value = {
            "current_transport_state": "STOPPED"
        }
        playing = MagicMock()
        playing.get_current_transport_info.return_value = {
            "current_transport_state": "PLAYING"
        }
        playing.get_current_track_info.return_value = {
            "uri": "x-sonos-spotify:track:123",
            "metadata": "",
        }
        with patch.object(
            self.bjornify.soco, "discover", return_value=[stopped, playing]
        ):
            self.assertIs(self.bjornify.find_playing_speaker(), playing)

        stopped.get_current_track_info.assert_not_called()

    def test_now_playing_uses_sonos_when_spotify_is_inactive(self):
        """A Spotify lookup failure returns Sonos playback in a Discord embed."""
        self.spotify.current_playback.return_value = None
        speaker = MagicMock()
        speaker.player_name = "Living Room"
        speaker.get_current_track_info.return_value = {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "duration": "3:00",
            "album_art_uri": "",
        }
        speaker.get_current_transport_info.return_value = {"current_position": "1:20"}
        with patch.object(self.bjornify, "find_playing_speaker", return_value=speaker):
            embed = self.bjornify.get_now_playing_embed()

        self.assertIn("Sonos Playback on Living Room", embed.description)
        self.assertIn("Artist", embed.description)

    def test_add_command_sends_queue_result(self):
        """The Discord text command sends the queue helper's response."""
        context = MagicMock()
        context.author.name = "Test user"
        context.send = AsyncMock()
        with patch.object(
            self.bjornify,
            "player_add_item_to_playback_queue",
            return_value="Queued: Artist - Song",
        ) as add:
            asyncio.run(self.bjornify.add_track.callback(context, query="Song"))

        add.assert_called_once_with("Song")
        context.send.assert_awaited_once_with("Queued: Artist - Song")

    def test_add_command_forwards_queue_failure(self):
        """A handled Spotify error reaches Discord as a readable response."""
        context = MagicMock()
        context.author.name = "Test user"
        context.send = AsyncMock()
        with patch.object(
            self.bjornify,
            "player_add_item_to_playback_queue",
            return_value="Failed to add track to queue.",
        ):
            asyncio.run(self.bjornify.add_track.callback(context, query="Song"))

        context.send.assert_awaited_once_with("Failed to add track to queue.")

    def test_spotify_request_recovers_current_playback_401(self):
        """401 on the initial GET also refreshes and retries once."""
        api_call = MagicMock(
            side_effect=[SpotifyException(401, -1, "Expired"), {"item": "ok"}]
        )
        with patch.object(self.bjornify, "refresh_spotify_token") as refresh:
            result = self.bjornify.spotify_request(api_call)
        self.assertEqual(result, {"item": "ok"})
        self.assertEqual(api_call.call_count, 2)
        refresh.assert_called_once_with(force=True)

    def test_spotify_request_does_not_loop_forever_on_401(self):
        """A persistent 401 must stop after one refresh and one retry."""
        api_call = MagicMock(side_effect=SpotifyException(401, -1, "No access"))
        with (
            patch.object(self.bjornify, "refresh_spotify_token") as refresh,
            self.assertRaises(SpotifyException),
        ):
            self.bjornify.spotify_request(api_call)
        self.assertEqual(api_call.call_count, 2)
        refresh.assert_called_once_with(force=True)

    def test_unauthorized_player_returns_reconnect_message(self):
        """Playback failures do not leak OAuth errors to Discord users."""
        self.spotify.current_playback.side_effect = (
            self.bjornify.SpotifyAuthorizationRequired("Token expired")
        )
        response = self.bjornify.player_pause_playback()
        self.assertIn("reconnected", response)

    def test_unrelated_403_does_not_try_sonos(self):
        """Premium/allowlist failures are not mistaken for Sonos restrictions."""
        self.spotify.current_playback.return_value = {"is_playing": True}
        action = MagicMock(
            side_effect=SpotifyException(403, -1, "Insufficient client scope")
        )
        with patch.object(self.bjornify, "find_playing_speaker") as find:
            response = self.bjornify.spotify_action_with_soco_fallback(
                spotify_action=action,
                soco_action=MagicMock(),
                action_name="pause",
            )
        self.assertIn("Premium", response)
        find.assert_not_called()

    def test_rate_limit_includes_retry_delay(self):
        """429 errors report Retry-After instead of a generic queue failure."""
        self.spotify.search.side_effect = SpotifyException(
            429, -1, "Rate limited", headers={"Retry-After": "16"}
        )
        self.assertIn(
            "16 seconds", self.bjornify.player_add_item_to_playback_queue("Song")
        )

    def test_quota_exceeded_is_distinct_from_rate_limit(self):
        """Development Mode quota errors get a specific diagnostic."""
        self.spotify.search.side_effect = SpotifyException(
            429, -1, "Too many requests", reason="QUOTA_EXCEEDED"
        )
        self.assertIn(
            "Development Mode API quota",
            self.bjornify.player_add_item_to_playback_queue("Song"),
        )

    def test_slash_add_does_not_claim_success_when_queue_fails(self):
        """A failed playback action never yields a false-positive success."""
        interaction = MagicMock()
        interaction.user.name = "Test"
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        self.spotify.track.return_value = {
            "name": "Song",
            "artists": [{"name": "Artist"}],
        }
        with patch.object(
            self.bjornify,
            "player_add_track",
            return_value="Spotify denied access (403).",
        ):
            asyncio.run(
                self.bjornify.add_slash.callback(interaction, query="spotify:track:123")
            )
        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once_with(
            "❌ Spotify denied access (403).", ephemeral=True
        )

    def test_slash_add_connect_accepted_response_is_success(self):
        """Slash /add reports a confirmed Connect endpoint acknowledgement."""
        interaction = MagicMock()
        interaction.user.name = "Test"
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        self.spotify.track.return_value = {
            "name": "Song",
            "artists": [{"name": "Artist"}],
        }
        with patch.object(
            self.bjornify,
            "player_add_track",
            return_value="Queued: Artist - Song (Spotify Connect accepted request)",
        ):
            asyncio.run(
                self.bjornify.add_slash.callback(interaction, query="spotify:track:123")
            )
        interaction.response.defer.assert_awaited_once()
        interaction.followup.send.assert_awaited_once_with(
            "✅ Queued: Artist - Song (Spotify Connect accepted request)",
            delete_after=10,
        )

    def test_only_discord_owner_can_start_connect_playback(self):
        """Nonowners cannot obtain private device-pairing challenges."""
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()
        with (
            patch.object(
                self.bjornify.bot, "is_owner", new_callable=AsyncMock
            ) as owner,
            patch.object(
                self.bjornify.spotify_playback_pairings,
                "begin",
                new_callable=AsyncMock,
            ) as begin,
        ):
            owner.return_value = False
            asyncio.run(self.bjornify.spotify_connect_playback.callback(interaction))
        begin.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        self.assertTrue(
            interaction.response.send_message.await_args.kwargs["ephemeral"]
        )

    def test_discord_playback_pairing_shows_ephemeral_code_no_tokens(self):
        """Owner gets a private Spotify Pair link, not the OAuth device secret."""
        interaction = MagicMock()
        interaction.user.id = 123
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        with (
            patch.object(
                self.bjornify.bot, "is_owner", new_callable=AsyncMock
            ) as owner,
            patch.object(
                self.bjornify.spotify_playback_pairings,
                "begin",
                new_callable=AsyncMock,
            ) as begin,
        ):
            owner.return_value = True
            begin.return_value = {"user_code": "ABC123", "expires_in": 600}
            asyncio.run(self.bjornify.spotify_connect_playback.callback(interaction))
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        content = interaction.edit_original_response.await_args.kwargs["content"]
        view = interaction.edit_original_response.await_args.kwargs["view"]
        self.assertIn("ABC123", content)
        self.assertIn("spotify.com/pair", view.children[0].url)
        self.assertNotIn("access_token", content)
        self.assertNotIn("device_code", content)

    def test_discord_status_displays_both_grants_without_token_contents(self):
        """Owner status shows both grants while never printing refresh tokens."""
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()
        with (
            patch.object(
                self.bjornify.bot, "is_owner", new_callable=AsyncMock
            ) as owner,
            patch.object(self.bjornify, "playback_grant_status") as status,
            patch.object(self.bjornify, "auth_manager") as auth,
        ):
            owner.return_value = True
            status.return_value = "Connected (playback grant saved)."
            auth.cache_handler.get_cached_token.return_value = {
                "refresh_token": "webapi-private-refresh-secret"
            }
            asyncio.run(self.bjornify.spotify_status.callback(interaction))
        output = interaction.response.send_message.await_args.args[0]
        self.assertIn("Spotify Web API", output)
        self.assertIn("Spotify Connect playback", output)
        self.assertNotIn("webapi-private-refresh-secret", output)
        self.assertTrue(
            interaction.response.send_message.await_args.kwargs["ephemeral"]
        )

    def test_slash_pause_does_not_claim_success_on_failure(self):
        """Pause slash command reports authentication failures accurately."""
        interaction = MagicMock()
        interaction.user.name = "Test"
        interaction.response.send_message = AsyncMock()
        with patch.object(
            self.bjornify, "player_pause_playback", return_value="Access denied."
        ):
            asyncio.run(self.bjornify.pause_slash.callback(interaction))
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        interaction.followup.send.assert_awaited_once_with(
            "❌ Access denied.", ephemeral=True
        )

    def test_restricted_sonos_control_targets_exact_room(self):
        """A restricted device must not control an unrelated Sonos room."""
        self.spotify.current_playback.return_value = {
            "device": {"name": "Family room", "type": "Speaker"}
        }
        action = MagicMock(side_effect=SpotifyException(403, -1, "Restricted device"))
        speaker = MagicMock()
        control = MagicMock()
        with patch.object(self.bjornify, "find_playing_speaker", return_value=speaker) as find:
            result = self.bjornify.spotify_action_with_soco_fallback(
                spotify_action=action, soco_action=control, action_name="pause"
            )
        self.assertEqual(result, "✅")
        find.assert_called_once_with("Family room")
        control.assert_called_once_with(speaker)

    def test_track_uri_text_command_uses_same_queue_helper(self):
        """Text commands support direct Spotify track URIs."""
        self.spotify.track.return_value = {
            "name": "Song", "artists": [{"name": "Artist"}]
        }
        with patch.object(self.bjornify, "player_add_track", return_value="Queued: Artist - Song") as add:
            result = self.bjornify.player_add_item_to_playback_queue("spotify:track:123")
        self.assertEqual(result, "Queued: Artist - Song")
        add.assert_called_once_with("spotify:track:123", "Artist", "Song")

    def test_device_specific_start_failure_tries_next_device(self):
        """A known device-specific rejection permits trying another device."""
        self.spotify.current_playback.return_value = None
        self.spotify.devices.return_value = {"devices": [
            {"name": "Everywhere", "id": "bad"},
            {"name": "Kitchen", "id": "good"},
        ]}
        self.spotify.start_playback.side_effect = [
            SpotifyException(403, -1, "Restricted device"), None
        ]
        result = self.bjornify.player_add_track("spotify:track:123")
        self.assertTrue(result.startswith("Started playback:"))
        self.assertEqual(self.spotify.start_playback.call_count, 2)

    def test_sonos_ambiguous_rooms_fail_closed(self):
        """Two active Spotify rooms must not be chosen arbitrarily."""
        speakers = [MagicMock(), MagicMock()]
        for speaker in speakers:
            speaker.get_current_transport_info.return_value = {
                "current_transport_state": "PLAYING"
            }
            speaker.get_current_track_info.return_value = {
                "uri": "x-sonos-spotify:track:abc", "metadata": ""
            }
        with patch.object(self.bjornify.soco, "discover", return_value=speakers):
            self.assertIsNone(self.bjornify.find_playing_speaker())

    def test_home_assistant_success(self):
        """Assist responses are extracted and authentication is supplied."""
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "response": {"speech": {"plain": {"speech": "The lights are on."}}}
        }

        with patch.object(self.hass.requests, "post", return_value=response) as post:
            result = self.hass.send_query_to_ha_assist("Turn on the lights")

        self.assertEqual(result, "The lights are on.")
        post.assert_called_once_with(
            "https://homeassistant.invalid/api/conversation/process",
            json={"text": "Turn on the lights", "language": "en"},
            headers={
                "Authorization": "Bearer test-ha-token",
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    def test_home_assistant_http_failure(self):
        """Non-successful HTTP responses are handled without JSON parsing."""
        response = MagicMock(status_code=503)
        with patch.object(self.hass.requests, "post", return_value=response):
            result = self.hass.send_query_to_ha_assist("Status")

        self.assertEqual(result, API_ERROR)
        response.json.assert_not_called()

    def test_home_assistant_timeout(self):
        """Network timeouts are converted to a readable bot response."""
        with patch.object(
            self.hass.requests,
            "post",
            side_effect=requests.Timeout("no response"),
        ):
            result = self.hass.send_query_to_ha_assist("Status")

        self.assertEqual(result, API_ERROR)


if __name__ == "__main__":
    unittest.main()
